"""Settings, read from environment variables only."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    # Secrets (Cloud Run injects these from Secret Manager in cdsci-infra).
    github_app_id: str
    github_app_private_key: str
    github_webhook_secret: str

    # Which jobs may get a GPU VM.
    allowed_repo: str = "seandavi/bioc-build"
    gpu_label: str = "gpu-l4"
    allowed_events: tuple[str, ...] = ("schedule", "workflow_dispatch")
    allowed_workflow_paths: tuple[str, ...] = (
        ".github/workflows/dispatch.yml",
        ".github/workflows/build-package.yml",
        ".github/workflows/gpu-smoke.yml",
    )

    # GCE.
    gcp_project: str = "bioc-u24"
    zones: tuple[str, ...] = ("us-central1-a", "us-central1-b", "us-central1-c")
    machine_type: str = "g2-standard-8"
    image_family: str = "gh-runner-gpu"
    disk_size_gb: int = 200
    max_run_duration_seconds: int = 6 * 3600
    runner_service_account: str = ""
    runner_group_id: int = 1

    # Sweep backstop. /sweep is disabled unless both OIDC settings are set.
    sweep_max_age_seconds: int = 7 * 3600
    sweep_oidc_audience: str = ""
    sweep_caller_email: str = ""

    github_api_url: str = "https://api.github.com"
    compute_api_url: str = "https://compute.googleapis.com/compute/v1"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env

        def req(name: str) -> str:
            value = e.get(name, "")
            if not value:
                raise RuntimeError(f"missing required environment variable {name}")
            return value

        defaults = cls.__dataclass_fields__

        def opt(name: str, field: str) -> str:
            return e.get(name) or str(defaults[field].default)

        return cls(
            github_app_id=req("GITHUB_APP_ID"),
            github_app_private_key=req("GITHUB_APP_PRIVATE_KEY"),
            github_webhook_secret=req("GITHUB_WEBHOOK_SECRET"),
            allowed_repo=opt("ALLOWED_REPO", "allowed_repo"),
            gpu_label=opt("GPU_LABEL", "gpu_label"),
            allowed_events=_csv(e["ALLOWED_EVENTS"])
            if e.get("ALLOWED_EVENTS")
            else defaults["allowed_events"].default,
            allowed_workflow_paths=_csv(e["ALLOWED_WORKFLOW_PATHS"])
            if e.get("ALLOWED_WORKFLOW_PATHS")
            else defaults["allowed_workflow_paths"].default,
            gcp_project=opt("GCP_PROJECT", "gcp_project"),
            zones=_csv(e["ZONES"]) if e.get("ZONES") else defaults["zones"].default,
            machine_type=opt("MACHINE_TYPE", "machine_type"),
            image_family=opt("IMAGE_FAMILY", "image_family"),
            disk_size_gb=int(opt("DISK_SIZE_GB", "disk_size_gb")),
            max_run_duration_seconds=int(
                opt("MAX_RUN_DURATION_SECONDS", "max_run_duration_seconds")
            ),
            runner_service_account=req("RUNNER_SERVICE_ACCOUNT"),
            runner_group_id=int(opt("RUNNER_GROUP_ID", "runner_group_id")),
            sweep_max_age_seconds=int(opt("SWEEP_MAX_AGE_SECONDS", "sweep_max_age_seconds")),
            sweep_oidc_audience=e.get("SWEEP_OIDC_AUDIENCE", ""),
            sweep_caller_email=e.get("SWEEP_CALLER_EMAIL", ""),
        )
