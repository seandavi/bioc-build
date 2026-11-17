"""GitHub REST calls made as the dispatcher's GitHub App."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
import jwt
from tenacity.wait import wait_base

from .config import Settings
from .decide import jitconfig_request
from .retry import default_wait, request_with_retry


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        *,
        wait: wait_base | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._s = settings
        self._http = http
        self._wait = wait or default_wait()
        self._clock = clock
        self._token: str | None = None
        self._token_expires: float = 0.0

    def _app_jwt(self) -> str:
        now = int(self._clock())
        key = self._s.github_app_private_key.replace("\\n", "\n")
        # iat is back-dated 60 s for clock skew, as GitHub recommends; max lifetime is 10 min.
        claims = {"iat": now - 60, "exp": now + 9 * 60, "iss": self._s.github_app_id}
        return jwt.encode(claims, key, algorithm="RS256")

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _call(
        self, method: str, path: str, token: str, *, retry: bool = True, **kwargs
    ) -> httpx.Response:
        url = f"{self._s.github_api_url}{path}"
        headers = self._headers(token)
        if retry:
            return await request_with_retry(
                self._http, method, url, wait=self._wait, headers=headers, **kwargs
            )
        return await self._http.request(method, url, headers=headers, **kwargs)

    async def installation_token(self) -> str:
        """Installation access token for the allowed repo, cached until shortly before expiry."""
        if self._token and self._clock() < self._token_expires - 60:
            return self._token
        app_jwt = self._app_jwt()
        r = await self._call("GET", f"/repos/{self._s.allowed_repo}/installation", app_jwt)
        if r.status_code != 200:
            raise GitHubError(f"installation lookup failed: HTTP {r.status_code}", r.status_code)
        installation_id = r.json()["id"]
        r = await self._call("POST", f"/app/installations/{installation_id}/access_tokens", app_jwt)
        if r.status_code != 201:
            raise GitHubError(f"installation token failed: HTTP {r.status_code}", r.status_code)
        body = r.json()
        self._token = body["token"]
        self._token_expires = datetime.fromisoformat(body["expires_at"]).timestamp()
        return self._token

    async def get_run(self, repo: str, run_id: int) -> dict[str, Any]:
        token = await self.installation_token()
        r = await self._call("GET", f"/repos/{repo}/actions/runs/{run_id}", token)
        if r.status_code != 200:
            raise GitHubError(f"get run {run_id} failed: HTTP {r.status_code}", r.status_code)
        return r.json()

    async def generate_jitconfig(self, repo: str, job_id: int) -> str:
        """Create a single-use runner registration; returns the encoded JIT config.

        Not retried: a repeat after a lost response would 409 on the runner name.
        """
        token = await self.installation_token()
        r = await self._call(
            "POST",
            f"/repos/{repo}/actions/runners/generate-jitconfig",
            token,
            retry=False,
            json=jitconfig_request(self._s, job_id),
        )
        if r.status_code != 201:
            raise GitHubError(
                f"generate-jitconfig for job {job_id} failed: HTTP {r.status_code} {r.text[:200]}",
                r.status_code,
            )
        return r.json()["encoded_jit_config"]
