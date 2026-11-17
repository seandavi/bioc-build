"""GitHub and Compute clients and the Dispatcher, with HTTP mocked by respx."""

import dataclasses
import json
from datetime import UTC, datetime

import httpx
import jwt
import pytest
import respx
from conftest import good_run, queued_payload
from tenacity import wait_none

from gpu_dispatcher.compute import ComputeClient, ComputeError
from gpu_dispatcher.decide import Candidate
from gpu_dispatcher.github import GitHubClient, GitHubError
from gpu_dispatcher.service import Dispatcher

GH = "https://api.github.com"
CE = "https://compute.googleapis.com/compute/v1/projects/bioc-u24"
REPO = "seandavi/bioc-build"
FUTURE = "2099-01-01T00:00:00Z"


async def fake_token() -> str:
    return "gce-token"


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def github(settings, http):
    return GitHubClient(settings, http, wait=wait_none())


@pytest.fixture
def compute(settings, http):
    return ComputeClient(settings, http, fake_token, wait=wait_none(), operation_timeout=5)


@pytest.fixture
def dispatcher(settings, github, compute):
    return Dispatcher(settings, github, compute)


def mock_github_auth(mock):
    mock.get(f"{GH}/repos/{REPO}/installation").respond(200, json={"id": 7})
    mock.post(f"{GH}/app/installations/7/access_tokens").respond(
        201, json={"token": "inst-token", "expires_at": FUTURE}
    )


def done_op(name="op1", **extra):
    return {"name": name, "status": "DONE", **extra}


def failed_op(code="ZONE_RESOURCE_POOL_EXHAUSTED"):
    return done_op(error={"errors": [{"code": code, "message": "no capacity"}]})


def insert_url(zone):
    return f"{CE}/zones/{zone}/instances"


def agg_url():
    return f"{CE}/aggregated/instances"


class TestGitHubClient:
    @respx.mock
    async def test_installation_token_flow_and_cache(self, github, settings):
        mock_github_auth(respx)
        run = respx.get(f"{GH}/repos/{REPO}/actions/runs/222").respond(200, json=good_run())
        assert (await github.get_run(REPO, 222))["event"] == "workflow_dispatch"
        await github.get_run(REPO, 222)
        assert run.call_count == 2
        assert respx.routes[0].call_count == 1  # installation looked up once (cached token)
        assert run.calls[0].request.headers["authorization"] == "Bearer inst-token"
        # The App JWT is RS256, issued by the app id.
        app_auth = respx.routes[0].calls[0].request.headers["authorization"].removeprefix("Bearer ")
        claims = jwt.decode(app_auth, options={"verify_signature": False})
        assert claims["iss"] == "12345"

    @respx.mock
    async def test_private_key_with_escaped_newlines(self, settings, http):
        s = dataclasses.replace(
            settings, github_app_private_key=settings.github_app_private_key.replace("\n", "\\n")
        )
        mock_github_auth(respx)
        respx.get(f"{GH}/repos/{REPO}/actions/runs/1").respond(200, json={})
        await GitHubClient(s, http, wait=wait_none()).get_run(REPO, 1)

    @respx.mock
    async def test_get_run_retries_5xx_then_succeeds(self, github):
        mock_github_auth(respx)
        route = respx.get(f"{GH}/repos/{REPO}/actions/runs/222")
        route.side_effect = [httpx.Response(502), httpx.Response(200, json=good_run())]
        assert (await github.get_run(REPO, 222))["id"] == 222

    @respx.mock
    async def test_get_run_404_raises(self, github):
        mock_github_auth(respx)
        respx.get(f"{GH}/repos/{REPO}/actions/runs/222").respond(404)
        with pytest.raises(GitHubError) as exc:
            await github.get_run(REPO, 222)
        assert exc.value.status == 404

    @respx.mock
    async def test_generate_jitconfig_request_and_no_retry(self, github):
        mock_github_auth(respx)
        route = respx.post(f"{GH}/repos/{REPO}/actions/runners/generate-jitconfig")
        route.respond(201, json={"encoded_jit_config": "ENC"})
        assert await github.generate_jitconfig(REPO, 111) == "ENC"
        assert json.loads(route.calls[0].request.content) == {
            "name": "gpu-111",
            "runner_group_id": 1,
            "labels": ["self-hosted", "linux", "x64", "gpu-l4"],
        }
        route.respond(500)
        with pytest.raises(GitHubError):
            await github.generate_jitconfig(REPO, 111)
        assert route.call_count == 2  # one try each, not retried


class TestComputeClient:
    @respx.mock
    async def test_insert_waits_for_operation(self, compute):
        respx.post(insert_url("z1")).respond(200, json={"name": "op1", "status": "RUNNING"})
        wait = respx.post(f"{CE}/zones/z1/operations/op1/wait").respond(200, json=done_op())
        assert await compute.insert_instance("z1", {"name": "x"}) is True
        assert wait.called
        assert wait.calls[0].request.headers["authorization"] == "Bearer gce-token"

    @respx.mock
    async def test_insert_capacity_error_in_operation(self, compute):
        respx.post(insert_url("z1")).respond(200, json=failed_op())
        with pytest.raises(ComputeError) as exc:
            await compute.insert_instance("z1", {})
        assert exc.value.code == "ZONE_RESOURCE_POOL_EXHAUSTED"

    @respx.mock
    async def test_insert_http_409_is_already_exists(self, compute):
        respx.post(insert_url("z1")).respond(409, json={"error": {}})
        assert await compute.insert_instance("z1", {}) is False

    @respx.mock
    async def test_insert_operation_already_exists(self, compute):
        respx.post(insert_url("z1")).respond(200, json=failed_op("RESOURCE_ALREADY_EXISTS"))
        assert await compute.insert_instance("z1", {}) is False

    @respx.mock
    async def test_insert_http_quota_error(self, compute):
        respx.post(insert_url("z1")).respond(
            403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}}
        )
        with pytest.raises(ComputeError) as exc:
            await compute.insert_instance("z1", {})
        assert exc.value.code == "quotaExceeded"

    @respx.mock
    async def test_list_runner_instances_pages(self, compute):
        route = respx.get(agg_url())
        route.side_effect = [
            httpx.Response(
                200,
                json={
                    "items": {
                        "zones/z1": {
                            "instances": [
                                {"name": "gh-runner-1", "creationTimestamp": "2026-01-01T00:00:00Z"}
                            ]
                        }
                    },
                    "nextPageToken": "p2",
                },
            ),
            httpx.Response(
                200,
                json={
                    "items": {
                        "zones/z2": {
                            "instances": [
                                {"name": "gh-runner-2", "creationTimestamp": "2026-01-01T00:00:00Z"}
                            ]
                        },
                        "zones/z3": {"warning": {"code": "NO_RESULTS_ON_PAGE"}},
                    }
                },
            ),
        ]
        found = await compute.list_runner_instances()
        assert [(i.zone, i.name) for i in found] == [("z1", "gh-runner-1"), ("z2", "gh-runner-2")]
        assert route.calls[0].request.url.params["filter"] == 'labels.purpose = "gh-runner"'
        assert route.calls[1].request.url.params["pageToken"] == "p2"

    @respx.mock
    async def test_delete_404_is_fine(self, compute):
        respx.delete(f"{CE}/zones/z1/instances/n").respond(404)
        await compute.delete_instance("z1", "n")


def candidate():
    return Candidate(job_id=111, run_id=222, repo=REPO)


def no_instances():
    return httpx.Response(200, json={"items": {"zones/z": {"warning": {}}}})


class TestTriage:
    @respx.mock
    async def test_allowed_run(self, dispatcher):
        mock_github_auth(respx)
        respx.get(f"{GH}/repos/{REPO}/actions/runs/222").respond(200, json=good_run())
        out = await dispatcher.triage("workflow_job", queued_payload())
        assert out.candidate == candidate()

    @respx.mock
    async def test_fork_pull_request_run_is_refused(self, dispatcher):
        mock_github_auth(respx)
        respx.get(f"{GH}/repos/{REPO}/actions/runs/222").respond(
            200,
            json=good_run(
                event="pull_request",
                head_repository={"full_name": "attacker/bioc-build"},
                path=".github/workflows/gpu-smoke.yml",
            ),
        )
        out = await dispatcher.triage("workflow_job", queued_payload())
        assert out.candidate is None

    @respx.mock
    async def test_event_filter_makes_no_github_call(self, dispatcher):
        out = await dispatcher.triage("workflow_job", queued_payload(action="completed"))
        assert out.candidate is None
        assert not respx.calls


class TestLaunch:
    @respx.mock
    async def test_spot_first_zone(self, dispatcher):
        mock_github_auth(respx)
        respx.get(agg_url()).mock(return_value=no_instances())
        respx.post(f"{GH}/repos/{REPO}/actions/runners/generate-jitconfig").respond(
            201, json={"encoded_jit_config": "ENC"}
        )
        ins = respx.post(insert_url("us-central1-a")).respond(200, json=done_op())
        result = await dispatcher.launch(candidate())
        assert "created (spot, us-central1-a)" in result
        body = json.loads(ins.calls[0].request.content)
        assert body["name"] == "gh-runner-111"
        assert body["scheduling"]["provisioningModel"] == "SPOT"
        assert body["metadata"]["items"][0]["value"] == "ENC"

    @respx.mock
    async def test_falls_through_spot_zones_then_on_demand(self, dispatcher):
        mock_github_auth(respx)
        respx.get(agg_url()).mock(return_value=no_instances())
        respx.post(f"{GH}/repos/{REPO}/actions/runners/generate-jitconfig").respond(
            201, json={"encoded_jit_config": "ENC"}
        )
        calls = []
        seq = {
            "us-central1-a": [failed_op(), failed_op()],
            "us-central1-b": [failed_op(), done_op()],
            "us-central1-c": [failed_op(), failed_op()],
        }
        for zone, ops in seq.items():
            route = respx.post(insert_url(zone))
            route.side_effect = [httpx.Response(200, json=op) for op in ops]
            calls.append((zone, route))
        result = await dispatcher.launch(candidate())
        assert "created (on-demand, us-central1-b)" in result
        kinds = {
            (zone, json.loads(c.request.content)["scheduling"]["provisioningModel"])
            for zone, route in calls
            for c in route.calls
        }
        assert kinds == {
            ("us-central1-a", "SPOT"),
            ("us-central1-b", "SPOT"),
            ("us-central1-c", "SPOT"),
            ("us-central1-b", "STANDARD"),
            ("us-central1-a", "STANDARD"),
        }
        # Order: all spot zones were tried before the first on-demand attempt.
        order = [
            json.loads(c.request.content)["scheduling"]["provisioningModel"]
            for c in respx.calls
            if c.request.url.path.endswith("/instances") and c.request.method == "POST"
        ]
        assert order == ["SPOT", "SPOT", "SPOT", "STANDARD", "STANDARD"]

    @respx.mock
    async def test_all_attempts_fail(self, dispatcher):
        mock_github_auth(respx)
        respx.get(agg_url()).mock(return_value=no_instances())
        respx.post(f"{GH}/repos/{REPO}/actions/runners/generate-jitconfig").respond(
            201, json={"encoded_jit_config": "ENC"}
        )
        for zone in ("a", "b", "c"):
            respx.post(insert_url(f"us-central1-{zone}")).respond(200, json=failed_op())
        with pytest.raises(ComputeError, match="no capacity"):
            await dispatcher.launch(candidate())

    @respx.mock
    async def test_redelivered_webhook_existing_instance_creates_nothing(self, dispatcher):
        respx.get(agg_url()).respond(
            200,
            json={
                "items": {
                    "zones/us-central1-a": {
                        "instances": [
                            {"name": "gh-runner-111", "creationTimestamp": "2026-01-01T00:00:00Z"}
                        ]
                    }
                }
            },
        )
        result = await dispatcher.launch(candidate())
        assert "already exists" in result
        assert len(respx.calls) == 1  # no jitconfig, no insert

    @respx.mock
    async def test_409_on_insert_is_success(self, dispatcher):
        mock_github_auth(respx)
        respx.get(agg_url()).mock(return_value=no_instances())
        respx.post(f"{GH}/repos/{REPO}/actions/runners/generate-jitconfig").respond(
            201, json={"encoded_jit_config": "ENC"}
        )
        respx.post(insert_url("us-central1-a")).respond(409)
        assert "already existed" in await dispatcher.launch(candidate())

    @respx.mock
    async def test_launch_safely_swallows_errors(self, dispatcher):
        respx.get(agg_url()).respond(500)
        await dispatcher.launch_safely(candidate())  # must not raise


class TestSweep:
    @respx.mock
    async def test_deletes_only_stale(self, dispatcher):
        respx.get(agg_url()).respond(
            200,
            json={
                "items": {
                    "zones/z1": {
                        "instances": [
                            {"name": "old", "creationTimestamp": "2026-11-17T00:00:00+00:00"},
                            {"name": "new", "creationTimestamp": "2026-11-17T11:00:00+00:00"},
                        ]
                    }
                }
            },
        )
        old = respx.delete(f"{CE}/zones/z1/instances/old").respond(200, json={})
        new = respx.delete(f"{CE}/zones/z1/instances/new").respond(200, json={})
        deleted = await dispatcher.sweep(datetime(2026, 11, 17, 12, 0, tzinfo=UTC))
        assert deleted == ["old"]
        assert old.called and not new.called
