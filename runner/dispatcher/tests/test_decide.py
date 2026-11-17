import hashlib
import hmac
from datetime import UTC, datetime

import pytest
from conftest import good_run, queued_payload

from gpu_dispatcher.decide import (
    Attempt,
    Candidate,
    Ignore,
    attempt_order,
    evaluate_event,
    evaluate_run,
    instance_body,
    instance_name,
    is_stale,
    jitconfig_request,
    verify_signature,
)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_valid(self):
        assert verify_signature("k", b"{}", sign("k", b"{}"))

    @pytest.mark.parametrize("header", [None, "", "sha256=", "sha1=abc", "abc", "sha256=00"])
    def test_malformed_or_wrong(self, header):
        assert not verify_signature("k", b"{}", header)

    def test_wrong_secret_and_wrong_body(self):
        assert not verify_signature("other", b"{}", sign("k", b"{}"))
        assert not verify_signature("k", b"{ }", sign("k", b"{}"))

    def test_empty_secret_never_verifies(self):
        assert not verify_signature("", b"{}", sign("", b"{}"))

    def test_uses_constant_time_compare(self, monkeypatch):
        calls = []
        real = hmac.compare_digest
        monkeypatch.setattr(hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
        verify_signature("k", b"{}", sign("k", b"{}"))
        assert calls


class TestEvaluateEvent:
    def test_queued_gpu_job_is_candidate(self, settings):
        result = evaluate_event("workflow_job", queued_payload(), settings)
        assert result == Candidate(job_id=111, run_id=222, repo="seandavi/bioc-build")

    def test_other_event(self, settings):
        assert isinstance(evaluate_event("push", queued_payload(), settings), Ignore)
        assert isinstance(evaluate_event(None, queued_payload(), settings), Ignore)

    @pytest.mark.parametrize("action", ["in_progress", "completed", "waiting", None])
    def test_other_action(self, settings, action):
        assert isinstance(
            evaluate_event("workflow_job", queued_payload(action=action), settings), Ignore
        )

    def test_missing_gpu_label(self, settings):
        p = queued_payload()
        p["workflow_job"]["labels"] = ["self-hosted", "linux"]
        assert isinstance(evaluate_event("workflow_job", p, settings), Ignore)

    def test_no_labels_key(self, settings):
        p = queued_payload()
        del p["workflow_job"]["labels"]
        assert isinstance(evaluate_event("workflow_job", p, settings), Ignore)

    def test_other_repo(self, settings):
        p = queued_payload(repository={"full_name": "evil/bioc-build"})
        result = evaluate_event("workflow_job", p, settings)
        assert isinstance(result, Ignore) and "not allowed" in result.reason

    def test_repo_comparison_ignores_case(self, settings):
        p = queued_payload(repository={"full_name": "SeanDavi/Bioc-Build"})
        assert isinstance(evaluate_event("workflow_job", p, settings), Candidate)

    def test_missing_ids(self, settings):
        p = queued_payload()
        del p["workflow_job"]["run_id"]
        assert isinstance(evaluate_event("workflow_job", p, settings), Ignore)

    def test_no_workflow_job(self, settings):
        assert isinstance(
            evaluate_event("workflow_job", {"action": "queued", "repository": {}}, settings),
            Ignore,
        )

    def test_custom_label(self, settings):
        import dataclasses

        s = dataclasses.replace(settings, gpu_label="gpu-a100")
        assert isinstance(evaluate_event("workflow_job", queued_payload(), s), Ignore)


class TestEvaluateRun:
    """The guard against a fork pull request asking for the GPU label."""

    def test_allowed_dispatch_and_schedule(self, settings):
        assert evaluate_run(good_run(), settings) is None
        assert evaluate_run(good_run(event="schedule"), settings) is None

    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/dispatch.yml",
            ".github/workflows/build-package.yml",
            ".github/workflows/gpu-smoke.yml",
            ".github/workflows/gpu-smoke.yml@refs/heads/main",
        ],
    )
    def test_allowed_paths(self, settings, path):
        assert evaluate_run(good_run(path=path), settings) is None

    @pytest.mark.parametrize(
        "event",
        [
            "pull_request",
            "pull_request_target",
            "push",
            "workflow_run",
            "issue_comment",
            None,
        ],
    )
    def test_disallowed_events(self, settings, event):
        assert isinstance(evaluate_run(good_run(event=event), settings), Ignore)

    def test_fork_head_repository(self, settings):
        run = good_run(head_repository={"full_name": "attacker/bioc-build"})
        result = evaluate_run(run, settings)
        assert isinstance(result, Ignore) and "attacker/bioc-build" in result.reason

    def test_fork_pull_request_is_refused_on_every_count(self, settings):
        run = good_run(
            event="pull_request",
            head_repository={"full_name": "attacker/bioc-build"},
            path=".github/workflows/evil.yml",
        )
        assert isinstance(evaluate_run(run, settings), Ignore)

    @pytest.mark.parametrize("head", [None, {}, {"full_name": None}])
    def test_missing_head_repository(self, settings, head):
        run = good_run()
        run["head_repository"] = head
        assert isinstance(evaluate_run(run, settings), Ignore)

    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/evil.yml",
            ".github/workflows/gpu-smoke.yml.evil",
            ".github/workflows/../workflows/gpu-smoke.yml",
            "gpu-smoke.yml",
            "x.github/workflows/gpu-smoke.yml",
            "",
            None,
        ],
    )
    def test_disallowed_paths(self, settings, path):
        assert isinstance(evaluate_run(good_run(path=path), settings), Ignore)


def test_instance_name_is_deterministic():
    assert instance_name(42) == instance_name(42) == "gh-runner-42"
    assert instance_name(42) != instance_name(43)


def test_attempt_order_spot_everywhere_then_on_demand():
    zones = ["a", "b", "c"]
    assert attempt_order(zones) == [
        Attempt("a", True),
        Attempt("b", True),
        Attempt("c", True),
        Attempt("a", False),
        Attempt("b", False),
        Attempt("c", False),
    ]


class TestInstanceBody:
    def test_spot(self, settings):
        b = instance_body(settings, 111, "JIT", Attempt("us-central1-b", True))
        assert b["name"] == "gh-runner-111"
        assert b["machineType"] == "zones/us-central1-b/machineTypes/g2-standard-8"
        sched = b["scheduling"]
        assert sched == {
            "provisioningModel": "SPOT",
            "onHostMaintenance": "TERMINATE",
            "automaticRestart": False,
            "instanceTerminationAction": "DELETE",
            "maxRunDuration": {"seconds": "21600"},
        }
        disk = b["disks"][0]
        assert disk["boot"] and disk["autoDelete"]
        assert disk["initializeParams"]["sourceImage"] == (
            "projects/bioc-u24/global/images/family/gh-runner-gpu"
        )
        assert disk["initializeParams"]["diskSizeGb"] == "200"
        assert disk["initializeParams"]["diskType"] == "zones/us-central1-b/diskTypes/pd-balanced"
        assert b["metadata"]["items"] == [{"key": "jit-config", "value": "JIT"}]
        assert b["labels"] == {"purpose": "gh-runner", "gh-job-id": "111"}
        assert b["serviceAccounts"][0]["email"] == settings.runner_service_account
        assert "guestAccelerators" not in b  # G2 includes its L4

    def test_on_demand(self, settings):
        b = instance_body(settings, 1, "J", Attempt("us-central1-a", False))
        assert b["scheduling"]["provisioningModel"] == "STANDARD"
        assert b["scheduling"]["instanceTerminationAction"] == "DELETE"
        assert b["scheduling"]["maxRunDuration"] == {"seconds": "21600"}


def test_jitconfig_request(settings):
    assert jitconfig_request(settings, 111) == {
        "name": "gpu-111",
        "runner_group_id": 1,
        "labels": ["self-hosted", "linux", "x64", "gpu-l4"],
    }


class TestIsStale:
    now = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)

    def test_old(self):
        assert is_stale("2026-11-17T02:00:00.000-08:00", self.now, 3600)  # 10:00Z, two hours old

    def test_young(self):
        assert not is_stale("2026-11-17T11:30:00.000+00:00", self.now, 3600)

    def test_boundary_is_not_stale(self):
        assert not is_stale("2026-11-17T11:00:00+00:00", self.now, 3600)

    def test_naive_timestamp_treated_as_utc(self):
        assert is_stale("2026-11-17T01:00:00", self.now, 3600)
