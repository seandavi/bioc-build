"""Compute Engine REST calls (instances insert, list, delete, operation wait)."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity.wait import wait_base

from .config import Settings
from .decide import RUNNER_LABEL_KEY, RUNNER_LABEL_VALUE
from .retry import default_wait, request_with_retry

log = logging.getLogger(__name__)

TokenProvider = Callable[[], Awaitable[str]]


class ComputeError(Exception):
    """An insert failed; `code` is the Compute error code (e.g. ZONE_RESOURCE_POOL_EXHAUSTED)."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RunnerInstance:
    zone: str
    name: str
    creation_timestamp: str


class ComputeClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        token_provider: TokenProvider,
        *,
        wait: wait_base | None = None,
        operation_timeout: float = 300.0,
    ) -> None:
        self._s = settings
        self._http = http
        self._token = token_provider
        self._wait = wait or default_wait()
        self._operation_timeout = operation_timeout

    def _url(self, path: str) -> str:
        return f"{self._s.compute_api_url}/projects/{self._s.gcp_project}{path}"

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        return await request_with_retry(
            self._http, method, self._url(path), wait=self._wait, headers=headers, **kwargs
        )

    async def _aggregated_instances(self, filter_expr: str) -> list[RunnerInstance]:
        found: list[RunnerInstance] = []
        page_token: str | None = None
        while True:
            params = {"filter": filter_expr}
            if page_token:
                params["pageToken"] = page_token
            r = await self._call("GET", "/aggregated/instances", params=params)
            if r.status_code != 200:
                raise ComputeError(f"instances list failed: HTTP {r.status_code} {r.text[:200]}")
            body = r.json()
            for scope, data in (body.get("items") or {}).items():
                for inst in data.get("instances", []):
                    found.append(
                        RunnerInstance(
                            zone=scope.removeprefix("zones/"),
                            name=inst["name"],
                            creation_timestamp=inst["creationTimestamp"],
                        )
                    )
            page_token = body.get("nextPageToken")
            if not page_token:
                return found

    async def instance_exists(self, name: str) -> bool:
        return bool(await self._aggregated_instances(f'name = "{name}"'))

    async def list_runner_instances(self) -> list[RunnerInstance]:
        return await self._aggregated_instances(
            f'labels.{RUNNER_LABEL_KEY} = "{RUNNER_LABEL_VALUE}"'
        )

    async def delete_instance(self, zone: str, name: str) -> None:
        r = await self._call("DELETE", f"/zones/{zone}/instances/{name}")
        if r.status_code not in (200, 404):
            raise ComputeError(f"delete {name} failed: HTTP {r.status_code} {r.text[:200]}")

    async def insert_instance(self, zone: str, body: dict[str, Any]) -> bool:
        """Create the instance and wait for the operation.

        Returns True if created, False if an instance of that name already existed.
        Raises ComputeError on capacity, quota or any other failure, so the caller
        can try the next attempt.
        """
        r = await self._call("POST", f"/zones/{zone}/instances", json=body)
        if r.status_code == 409:
            return False
        if r.status_code != 200:
            raise ComputeError(
                f"insert in {zone} failed: HTTP {r.status_code} {r.text[:300]}",
                _first_error_code(r),
            )
        op = r.json()
        deadline = time.monotonic() + self._operation_timeout
        while op.get("status") != "DONE":
            if time.monotonic() > deadline:
                raise ComputeError(f"operation {op.get('name')} in {zone} timed out")
            # The wait endpoint blocks server-side for up to two minutes.
            wr = await self._call("POST", f"/zones/{zone}/operations/{op['name']}/wait")
            if wr.status_code != 200:
                raise ComputeError(f"operation wait failed: HTTP {wr.status_code}")
            op = wr.json()
        errors = (op.get("error") or {}).get("errors") or []
        if not errors:
            return True
        codes = [e.get("code") for e in errors]
        if "RESOURCE_ALREADY_EXISTS" in codes:
            return False
        raise ComputeError(
            f"insert in {zone} failed: {errors[0].get('code')}: {errors[0].get('message')}",
            codes[0],
        )


def _first_error_code(response: httpx.Response) -> str | None:
    try:
        errors = response.json()["error"]["errors"]
        return errors[0].get("reason")
    except (ValueError, KeyError, IndexError, TypeError):
        return None
