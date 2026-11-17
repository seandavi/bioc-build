"""Orchestration: carries out the decisions in decide.py using the GitHub and Compute clients."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from .compute import ComputeClient, ComputeError
from .config import Settings
from .decide import (
    Candidate,
    Ignore,
    attempt_order,
    evaluate_event,
    evaluate_run,
    instance_body,
    instance_name,
    is_stale,
)
from .github import GitHubClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookOutcome:
    """What /webhook answers. `candidate` is set only when a VM should be launched."""

    reason: str
    candidate: Candidate | None = None


class Dispatcher:
    def __init__(self, settings: Settings, github: GitHubClient, compute: ComputeClient) -> None:
        self._s = settings
        self._github = github
        self._compute = compute

    async def triage(self, event: str | None, payload: dict) -> WebhookOutcome:
        """Cheap synchronous part: webhook filter, then the workflow-run guard."""
        first = evaluate_event(event, payload, self._s)
        if isinstance(first, Ignore):
            return WebhookOutcome(first.reason)
        run = await self._github.get_run(first.repo, first.run_id)
        refused = evaluate_run(run, self._s)
        if refused is not None:
            log.warning(
                "refusing GPU runner for job %s (run %s): %s",
                first.job_id,
                first.run_id,
                refused.reason,
            )
            return WebhookOutcome(refused.reason)
        return WebhookOutcome("dispatching", first)

    async def launch(self, candidate: Candidate) -> str:
        """Create one VM for the job. Idempotent per job id. Returns a short outcome string."""
        name = instance_name(candidate.job_id)
        if await self._compute.instance_exists(name):
            return f"{name} already exists"
        jit = await self._github.generate_jitconfig(candidate.repo, candidate.job_id)
        failures: list[str] = []
        for attempt in attempt_order(self._s.zones):
            kind = "spot" if attempt.spot else "on-demand"
            body = instance_body(self._s, candidate.job_id, jit, attempt)
            try:
                created = await self._compute.insert_instance(attempt.zone, body)
            except ComputeError as exc:
                log.warning(
                    "%s in %s failed for job %s: %s", kind, attempt.zone, candidate.job_id, exc
                )
                failures.append(f"{kind} {attempt.zone}: {exc}")
                if await self._compute.instance_exists(name):
                    # e.g. an operation timed out but the VM did appear.
                    return f"{name} exists after {kind} attempt in {attempt.zone}"
                continue
            verb = "created" if created else "already existed"
            log.info("%s %s (%s, %s)", name, verb, kind, attempt.zone)
            return f"{name} {verb} ({kind}, {attempt.zone})"
        raise ComputeError(f"no capacity for job {candidate.job_id}: " + "; ".join(failures))

    async def launch_safely(self, candidate: Candidate) -> None:
        """Background-task wrapper: nothing may escape, the webhook has already been answered."""
        try:
            log.info("launch job %s: %s", candidate.job_id, await self.launch(candidate))
        except Exception:
            log.exception("launch failed for job %s", candidate.job_id)

    async def sweep(self, now: datetime) -> list[str]:
        """Delete runner VMs older than the configured age; returns their names."""
        deleted: list[str] = []
        for inst in await self._compute.list_runner_instances():
            if is_stale(inst.creation_timestamp, now, self._s.sweep_max_age_seconds):
                await self._compute.delete_instance(inst.zone, inst.name)
                log.warning("swept stale runner VM %s in %s", inst.name, inst.zone)
                deleted.append(inst.name)
        return deleted
