import pytest

from gpu_dispatcher.config import Settings

REQUIRED = {
    "GITHUB_APP_ID": "1",
    "GITHUB_APP_PRIVATE_KEY": "k",
    "GITHUB_WEBHOOK_SECRET": "w",
    "RUNNER_SERVICE_ACCOUNT": "r@p.iam.gserviceaccount.com",
}


def test_defaults():
    s = Settings.from_env(REQUIRED)
    assert s.allowed_repo == "seandavi/bioc-build"
    assert s.gpu_label == "gpu-l4"
    assert s.allowed_events == ("schedule", "workflow_dispatch")
    assert ".github/workflows/gpu-smoke.yml" in s.allowed_workflow_paths
    assert s.zones == ("us-central1-a", "us-central1-b", "us-central1-c")
    assert s.max_run_duration_seconds == 6 * 3600
    assert s.gcp_project == "bioc-u24"


def test_overrides():
    s = Settings.from_env(
        REQUIRED
        | {
            "ZONES": "z1, z2",
            "ALLOWED_EVENTS": "workflow_dispatch",
            "MAX_RUN_DURATION_SECONDS": "60",
        }
    )
    assert s.zones == ("z1", "z2")
    assert s.allowed_events == ("workflow_dispatch",)
    assert s.max_run_duration_seconds == 60


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required(missing):
    env = {k: v for k, v in REQUIRED.items() if k != missing}
    with pytest.raises(RuntimeError, match=missing):
        Settings.from_env(env)
