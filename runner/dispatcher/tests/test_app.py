"""HTTP surface: signature, routing, background launch, sweep authentication."""

import hashlib
import hmac
import json

import httpx
import pytest
import respx
from conftest import good_run, queued_payload
from tenacity import wait_none

from gpu_dispatcher.app import create_app

GH = "https://api.github.com"
CE = "https://compute.googleapis.com/compute/v1/projects/bioc-u24"
REPO = "seandavi/bioc-build"


async def fake_token() -> str:
    return "gce-token"


def sign(body: bytes, secret: str = "s3cret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
async def client(settings):
    def verify(token: str) -> dict:
        if token == "good":
            return {"email": settings.sweep_caller_email, "email_verified": True}
        if token == "other":
            return {"email": "someone@else.example", "email_verified": True}
        raise ValueError("bad token")

    app = create_app(
        settings, token_provider=fake_token, oidc_verifier=verify, retry_wait=wait_none()
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def post_webhook(client, payload, *, event="workflow_job", signature=None):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "X-Hub-Signature-256": signature or sign(body)}
    return await client.post("/webhook", content=body, headers=headers)


def mock_happy_github_and_compute(insert_status=200):
    respx.route(host="test").pass_through()
    respx.get(f"{GH}/repos/{REPO}/installation").respond(200, json={"id": 7})
    respx.post(f"{GH}/app/installations/7/access_tokens").respond(
        201, json={"token": "t", "expires_at": "2099-01-01T00:00:00Z"}
    )
    respx.get(f"{GH}/repos/{REPO}/actions/runs/222").respond(200, json=good_run())
    respx.get(f"{CE}/aggregated/instances").respond(200, json={"items": {}})
    respx.post(f"{GH}/repos/{REPO}/actions/runners/generate-jitconfig").respond(
        201, json={"encoded_jit_config": "ENC"}
    )
    return respx.post(f"{CE}/zones/us-central1-a/instances").respond(
        insert_status, json={"name": "op", "status": "DONE"}
    )


async def test_healthz(client):
    assert (await client.get("/healthz")).json() == {"status": "ok"}


async def test_bad_signature_is_401_and_does_nothing(client):
    with respx.mock:
        r = await post_webhook(client, queued_payload(), signature="sha256=" + "0" * 64)
        assert r.status_code == 401
        assert not [c for c in respx.calls if c.request.url.host != "test"]


async def test_missing_signature_is_401(client):
    r = await client.post("/webhook", content=b"{}", headers={"X-GitHub-Event": "workflow_job"})
    assert r.status_code == 401


async def test_ping(client):
    r = await post_webhook(client, {"zen": "x"}, event="ping")
    assert r.status_code == 200 and r.json()["reason"] == "pong"


async def test_non_json_body_with_valid_signature(client):
    body = b"not json"
    r = await client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sign(body)})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        queued_payload(action="completed"),
        queued_payload(repository={"full_name": "evil/x"}),
    ],
)
async def test_ignored_returns_200_with_reason(client, payload):
    r = await post_webhook(client, payload)
    assert r.status_code == 200
    assert r.json()["dispatched"] is False and r.json()["reason"]


@respx.mock
async def test_queued_gpu_job_launches_vm_in_background(client):
    insert = mock_happy_github_and_compute()
    r = await post_webhook(client, queued_payload())
    assert r.status_code == 200 and r.json()["dispatched"] is True
    assert insert.call_count == 1  # ASGI transport awaits the background task


@respx.mock
async def test_fork_pull_request_gets_2xx_and_no_vm(client):
    insert = mock_happy_github_and_compute()
    respx.get(f"{GH}/repos/{REPO}/actions/runs/222").respond(
        200,
        json=good_run(event="pull_request", head_repository={"full_name": "attacker/bioc-build"}),
    )
    r = await post_webhook(client, queued_payload())
    assert r.status_code == 200
    assert r.json()["dispatched"] is False
    assert "pull_request" in r.json()["reason"]
    assert insert.call_count == 0
    jit = [c for c in respx.calls if "generate-jitconfig" in str(c.request.url)]
    assert jit == []


@respx.mock
async def test_github_failure_during_triage_is_502(client):
    mock_happy_github_and_compute()
    respx.get(f"{GH}/repos/{REPO}/actions/runs/222").respond(404)
    r = await post_webhook(client, queued_payload())
    assert r.status_code == 502


@respx.mock
async def test_background_failure_does_not_change_the_response(client):
    mock_happy_github_and_compute()
    respx.post(f"{CE}/zones/us-central1-a/instances").respond(
        200,
        json={"name": "op", "status": "DONE", "error": {"errors": [{"code": "QUOTA_EXCEEDED"}]}},
    )
    for z in "bc":
        respx.post(f"{CE}/zones/us-central1-{z}/instances").respond(
            200, json={"name": "op", "status": "DONE", "error": {"errors": [{"code": "X"}]}}
        )
    r = await post_webhook(client, queued_payload())
    assert r.status_code == 200 and r.json()["dispatched"] is True


class TestSweepRoute:
    async def test_no_token(self, client):
        assert (await client.post("/sweep")).status_code == 401

    async def test_bad_token(self, client):
        r = await client.post("/sweep", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

    async def test_wrong_caller(self, client):
        r = await client.post("/sweep", headers={"Authorization": "Bearer other"})
        assert r.status_code == 403

    @respx.mock
    async def test_good_caller(self, client):
        respx.route(host="test").pass_through()
        respx.get(f"{CE}/aggregated/instances").respond(200, json={"items": {}})
        r = await client.post("/sweep", headers={"Authorization": "Bearer good"})
        assert r.status_code == 200 and r.json() == {"deleted": []}


async def test_sweep_disabled_without_oidc_settings(settings):
    import dataclasses

    s = dataclasses.replace(settings, sweep_oidc_audience="", sweep_caller_email="")
    app = create_app(s, token_provider=fake_token)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/sweep", headers={"Authorization": "Bearer good"})
            assert r.status_code == 403
