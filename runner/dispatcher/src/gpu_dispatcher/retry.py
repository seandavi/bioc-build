"""One retrying HTTP request helper shared by the GitHub and Compute clients."""

from __future__ import annotations

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base


class _TransientStatus(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


def default_wait() -> wait_base:
    return wait_exponential(multiplier=0.5, max=8)


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    wait: wait_base,
    attempts: int = 4,
    **kwargs,
) -> httpx.Response:
    """Send a request, retrying transport errors, HTTP 429 and 5xx.

    Returns the final response even when it is an error status; the caller decides
    what a 4xx means. Only use for requests that are safe to repeat.
    """
    retrying = AsyncRetrying(
        retry=retry_if_exception_type((httpx.TransportError, _TransientStatus)),
        stop=stop_after_attempt(attempts),
        wait=wait,
        reraise=True,
    )
    try:
        async for attempt in retrying:
            with attempt:
                response = await client.request(method, url, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    raise _TransientStatus(response)
                return response
    except _TransientStatus as exc:
        return exc.response
    raise AssertionError("unreachable")  # pragma: no cover
