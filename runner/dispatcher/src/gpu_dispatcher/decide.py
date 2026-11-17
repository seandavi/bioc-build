"""Pure decisions: no I/O, no clock, no environment.

Everything the dispatcher decides about *whether* and *what* to create lives
here so it can be tested exhaustively. The I/O modules only carry these
decisions out.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import Settings

RUNNER_LABEL_KEY = "purpose"
RUNNER_LABEL_VALUE = "gh-runner"
JOB_ID_LABEL_KEY = "gh-job-id"


# --- webhook authenticity ----------------------------------------------------


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Check GitHub's `X-Hub-Signature-256` value ("sha256=<hex>") in constant time."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.encode())


# --- should we dispatch ------------------------------------------------------


@dataclass(frozen=True)
class Ignore:
    reason: str


@dataclass(frozen=True)
class Candidate:
    """A queued GPU job from the allowed repo; the run guard has not been applied yet."""

    job_id: int
    run_id: int
    repo: str


def evaluate_event(
    event: str | None, payload: Mapping[str, Any], settings: Settings
) -> Ignore | Candidate:
    """First filter, on the webhook alone: right event, action, label and repo."""
    if event != "workflow_job":
        return Ignore(f"ignored event {event!r}")
    if payload.get("action") != "queued":
        return Ignore(f"ignored action {payload.get('action')!r}")
    job = payload.get("workflow_job")
    if not isinstance(job, Mapping):
        return Ignore("payload has no workflow_job")
    labels = job.get("labels") or []
    if settings.gpu_label not in labels:
        return Ignore(f"job does not request label {settings.gpu_label!r}")
    repo = (payload.get("repository") or {}).get("full_name")
    if not isinstance(repo, str) or repo.lower() != settings.allowed_repo.lower():
        return Ignore(f"repository {repo!r} is not allowed")
    job_id, run_id = job.get("id"), job.get("run_id")
    if not isinstance(job_id, int) or not isinstance(run_id, int):
        return Ignore("job id or run id missing")
    return Candidate(job_id=job_id, run_id=run_id, repo=settings.allowed_repo)


def _workflow_file(path: str) -> str:
    # The runs API may return "path@refs/heads/main"; the file path is the part before "@".
    return path.split("@", 1)[0]


def evaluate_run(run: Mapping[str, Any], settings: Settings) -> Ignore | None:
    """Second filter, on the workflow run fetched from GitHub. None means allowed.

    This is the guard against a fork pull request asking for the GPU label: such a
    run has event `pull_request*` and/or a head repository other than ours, and a
    workflow file we do not list. All three conditions must hold.
    """
    event = run.get("event")
    if event not in settings.allowed_events:
        return Ignore(f"run event {event!r} is not allowed")
    head_repo = (run.get("head_repository") or {}).get("full_name")
    if not isinstance(head_repo, str) or head_repo.lower() != settings.allowed_repo.lower():
        return Ignore(f"run head repository {head_repo!r} is not {settings.allowed_repo!r}")
    path = run.get("path")
    if not isinstance(path, str) or _workflow_file(path) not in settings.allowed_workflow_paths:
        return Ignore(f"workflow {path!r} is not allowed")
    return None


# --- what to create ----------------------------------------------------------


def instance_name(job_id: int) -> str:
    """Deterministic, so a redelivered webhook maps to the same VM."""
    return f"gh-runner-{job_id}"


def runner_name(job_id: int) -> str:
    return f"gpu-{job_id}"


@dataclass(frozen=True)
class Attempt:
    zone: str
    spot: bool


def attempt_order(zones: Sequence[str]) -> list[Attempt]:
    """Spot in every zone first, then on-demand in every zone."""
    return [Attempt(z, True) for z in zones] + [Attempt(z, False) for z in zones]


def instance_body(
    settings: Settings, job_id: int, jit_config: str, attempt: Attempt
) -> dict[str, Any]:
    """The Compute `instances.insert` request body.

    G2 machine types come with their L4 attached, so no `guestAccelerators` entry
    (the API rejects one for the G2 series).
    """
    zone = attempt.zone
    scheduling: dict[str, Any] = {
        "provisioningModel": "SPOT" if attempt.spot else "STANDARD",
        "onHostMaintenance": "TERMINATE",
        "automaticRestart": False,
        "instanceTerminationAction": "DELETE",
        "maxRunDuration": {"seconds": str(settings.max_run_duration_seconds)},
    }
    return {
        "name": instance_name(job_id),
        "machineType": f"zones/{zone}/machineTypes/{settings.machine_type}",
        "disks": [
            {
                "boot": True,
                "autoDelete": True,
                "initializeParams": {
                    "sourceImage": (
                        f"projects/{settings.gcp_project}/global/images/family/{settings.image_family}"
                    ),
                    "diskSizeGb": str(settings.disk_size_gb),
                    "diskType": f"zones/{zone}/diskTypes/pd-balanced",
                },
            }
        ],
        "networkInterfaces": [
            {
                "network": "global/networks/default",
                "accessConfigs": [{"name": "External NAT", "type": "ONE_TO_ONE_NAT"}],
            }
        ],
        "scheduling": scheduling,
        "serviceAccounts": [
            {
                "email": settings.runner_service_account,
                "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
            }
        ],
        "labels": {RUNNER_LABEL_KEY: RUNNER_LABEL_VALUE, JOB_ID_LABEL_KEY: str(job_id)},
        "metadata": {"items": [{"key": "jit-config", "value": jit_config}]},
    }


def jitconfig_request(settings: Settings, job_id: int) -> dict[str, Any]:
    return {
        "name": runner_name(job_id),
        "runner_group_id": settings.runner_group_id,
        "labels": ["self-hosted", "linux", "x64", settings.gpu_label],
    }


# --- sweep -------------------------------------------------------------------


def parse_timestamp(value: str) -> datetime:
    """Parse a Compute RFC 3339 timestamp such as 2026-11-17T10:00:00.123-08:00."""
    return datetime.fromisoformat(value)


def is_stale(creation_timestamp: str, now: datetime, max_age_seconds: int) -> bool:
    created = parse_timestamp(creation_timestamp)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (now - created).total_seconds() > max_age_seconds
