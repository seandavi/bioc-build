"""Credentials: Application Default Credentials for Compute, OIDC verification for /sweep."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import google.auth
import google.auth.transport.requests
from google.oauth2 import id_token

TokenProvider = Callable[[], Any]
OidcVerifier = Callable[[str], dict[str, Any]]


class AdcTokenProvider:
    """Access token for the service's own identity (the Cloud Run service account)."""

    def __init__(self) -> None:
        self._credentials: Any = None  # resolved on first use, so startup never blocks
        self._request = google.auth.transport.requests.Request()
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        async with self._lock:
            if self._credentials is None:
                self._credentials, _ = await asyncio.to_thread(
                    google.auth.default, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            if not self._credentials.valid:
                await asyncio.to_thread(self._credentials.refresh, self._request)
            return self._credentials.token


def google_oidc_verifier(audience: str) -> OidcVerifier:
    """Verify a Google-signed ID token (what Cloud Scheduler sends) for this audience."""
    request = google.auth.transport.requests.Request()

    def verify(token: str) -> dict[str, Any]:
        return id_token.verify_oauth2_token(token, request, audience)

    return verify
