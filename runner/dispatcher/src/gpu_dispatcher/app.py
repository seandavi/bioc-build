"""FastAPI app: the webhook receiver and the sweep endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from tenacity.wait import wait_base

from .auth import AdcTokenProvider, OidcVerifier, google_oidc_verifier
from .compute import ComputeClient, TokenProvider
from .config import Settings
from .decide import verify_signature
from .github import GitHubClient, GitHubError
from .service import Dispatcher

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
    token_provider: TokenProvider | None = None,
    oidc_verifier: OidcVerifier | None = None,
    retry_wait: wait_base | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    """Build the app. Arguments other than `settings` exist so tests can inject fakes."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        s = settings or Settings.from_env()
        owns_client = http_client is None
        http = http_client or httpx.AsyncClient(timeout=30.0)
        app.state.settings = s
        app.state.dispatcher = Dispatcher(
            s,
            GitHubClient(s, http, wait=retry_wait),
            ComputeClient(s, http, token_provider or AdcTokenProvider(), wait=retry_wait),
        )
        app.state.verify_oidc = oidc_verifier or (
            google_oidc_verifier(s.sweep_oidc_audience) if s.sweep_oidc_audience else None
        )
        try:
            yield
        finally:
            if owns_client:
                await http.aclose()

    app = FastAPI(title="gpu-dispatcher", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(
        request: Request,
        background: BackgroundTasks,
        x_hub_signature_256: str | None = Header(default=None),
        x_github_event: str | None = Header(default=None),
    ) -> dict[str, Any]:
        s: Settings = request.app.state.settings
        dispatcher: Dispatcher = request.app.state.dispatcher
        body = await request.body()
        if not verify_signature(s.github_webhook_secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="bad signature")
        if x_github_event == "ping":
            return {"dispatched": False, "reason": "pong"}
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="body is not a JSON object")
        try:
            outcome = await dispatcher.triage(x_github_event, payload)
        except GitHubError as exc:
            log.error("triage failed: %s", exc)
            # 5xx: the job stays queued and the delivery shows as failed in the App's
            # "Recent deliveries" page, from where it can be redelivered.
            raise HTTPException(status_code=502, detail="could not check the workflow run") from exc
        if outcome.candidate is None:
            return {"dispatched": False, "reason": outcome.reason}
        background.add_task(dispatcher.launch_safely, outcome.candidate)
        return {"dispatched": True, "reason": outcome.reason, "job_id": outcome.candidate.job_id}

    @app.post("/sweep")
    async def sweep(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        s: Settings = request.app.state.settings
        verify: OidcVerifier | None = request.app.state.verify_oidc
        if verify is None or not s.sweep_caller_email:
            raise HTTPException(status_code=403, detail="sweep is not enabled")
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        try:
            claims = await asyncio.to_thread(verify, authorization[7:].strip())
        except ValueError:
            raise HTTPException(status_code=401, detail="invalid token") from None
        if claims.get("email") != s.sweep_caller_email or not claims.get("email_verified"):
            raise HTTPException(status_code=403, detail="caller not allowed")
        deleted = await request.app.state.dispatcher.sweep(now())
        return {"deleted": deleted}

    return app


def app_factory() -> FastAPI:
    """Entry point for uvicorn: `uvicorn --factory gpu_dispatcher.app:app_factory`."""
    logging.basicConfig(level=logging.INFO)
    return create_app()
