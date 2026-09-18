"""Shared HTTP client for source adapters (PLAN.md Phase 1.1 / 1.10).

Every upstream call goes through here so that three things are true everywhere:

* **TLS verification works against Indian government hosts.** Several serve an incomplete
  certificate chain; OpenSSL cannot fetch the missing intermediate, the OS can. The
  context is built via ``truststore``. Verification is never disabled — an unverifiable
  host is a failed fetch, because trusting an unverified government endpoint would make
  the evidence it produces worthless.
* **The raw bytes are kept.** Responses carry their payload so the archive can store
  exactly what the upstream said (Phase 1.10), which is what deterministic replay
  re-runs from.
* **Transient failure is retried, permanent failure is not.** Timeouts, connection errors
  and 5xx are retried with backoff; a 401/404 is a fact about the source, not a blip.
"""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

try:
    import truststore
except ImportError:  # pragma: no cover - optional
    truststore = None  # type: ignore[assignment]

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRIES = 2
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
USER_AGENT = "ORCA-SIH26176/0.1 (marine decision support; +contact via project repo)"


def build_ssl_context() -> ssl.SSLContext:
    """TLS context that can verify hosts serving an incomplete chain.

    See ``scripts/spikes/README.md`` for the diagnosis. Verification stays enabled in
    both branches.
    """
    if truststore is not None:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return ssl.create_default_context()


@dataclass(frozen=True)
class RawResponse:
    """An upstream response plus everything needed to archive and audit it."""

    url: str
    status_code: int
    content: bytes
    content_type: str
    retrieved_at: datetime
    elapsed_ms: float

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        """Parse the payload as JSON."""
        import json

        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class HttpError(RuntimeError):
    """Transport-level failure after retries were exhausted."""


class HttpFetcher:
    """Async HTTP client shared by adapters."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._timeout = timeout
        self._retries = retries
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> HttpFetcher:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=min(10.0, self._timeout)),
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
                verify=build_ssl_context(),
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, url: str, *, params: dict[str, Any] | None = None) -> RawResponse:
        """GET with retries on transient failures.

        Returns the response even for non-2xx status: a 401 from IMD is information the
        adapter needs in order to report *why* it degraded, not an exception to swallow.
        """
        if self._client is None:
            msg = "HttpFetcher must be used as an async context manager"
            raise RuntimeError(msg)

        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            started = asyncio.get_running_loop().time()
            try:
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._retries:
                    await asyncio.sleep(2**attempt * 0.5)
                    continue
                raise HttpError(f"{type(exc).__name__}: {exc}") from exc

            elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000
            if response.status_code in RETRYABLE_STATUS and attempt < self._retries:
                await asyncio.sleep(2**attempt * 0.5)
                continue

            return RawResponse(
                url=str(response.url),
                status_code=response.status_code,
                content=response.content,
                content_type=response.headers.get("content-type", "application/octet-stream"),
                retrieved_at=datetime.now(UTC),
                elapsed_ms=round(elapsed_ms, 1),
            )

        raise HttpError(str(last_error))  # pragma: no cover - loop always returns or raises
