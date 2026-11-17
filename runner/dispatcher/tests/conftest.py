import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from gpu_dispatcher.config import Settings


@pytest.fixture(scope="session")
def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def settings(private_key_pem: str) -> Settings:
    return Settings(
        github_app_id="12345",
        github_app_private_key=private_key_pem,
        github_webhook_secret="s3cret",
        runner_service_account="gh-runner@bioc-u24.iam.gserviceaccount.com",
        sweep_oidc_audience="https://dispatcher.example",
        sweep_caller_email="scheduler@bioc-u24.iam.gserviceaccount.com",
    )


def queued_payload(**overrides) -> dict:
    payload = {
        "action": "queued",
        "workflow_job": {"id": 111, "run_id": 222, "labels": ["self-hosted", "gpu-l4"]},
        "repository": {"full_name": "seandavi/bioc-build"},
    }
    payload.update(overrides)
    return payload


def good_run(**overrides) -> dict:
    run = {
        "id": 222,
        "event": "workflow_dispatch",
        "path": ".github/workflows/gpu-smoke.yml",
        "head_repository": {"full_name": "seandavi/bioc-build"},
    }
    run.update(overrides)
    return run
