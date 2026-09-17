"""Dependency probes for readiness (PLAN.md Phase 0.2).

Phase 0 checks **TCP reachability** of the dev-stack dependencies and says exactly
that in the health payload. It deliberately does not claim more: proving that the
database really carries PostGIS/TimescaleDB/pgvector is the job of
``scripts/verify-stack.sh``, and Phase 1 replaces these probes with real queries
once a driver is in the dependency set.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from urllib.parse import urlparse

from orca_schemas import ComponentHealth, HealthStatus

DEFAULT_PORTS = {"postgresql": 5432, "postgres": 5432, "redis": 6379, "http": 80, "https": 443}
PROBE_TIMEOUT_SECONDS = 2.0


def _host_port(url: str) -> tuple[str | None, int | None]:
    parsed = urlparse(url)
    if not parsed.hostname:
        return None, None
    port = parsed.port or DEFAULT_PORTS.get(parsed.scheme)
    return parsed.hostname, port


async def probe_tcp(
    name: str, url: str, timeout: float = PROBE_TIMEOUT_SECONDS
) -> ComponentHealth:
    """Return the condition of one dependency by opening a TCP connection to it."""
    host, port = _host_port(url)
    if host is None or port is None:
        return ComponentHealth(
            name=name, status=HealthStatus.DOWN, detail=f"cannot parse host/port from {name} url"
        )

    started = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except TimeoutError:
        return ComponentHealth(
            name=name, status=HealthStatus.DOWN, detail=f"tcp connect timed out after {timeout}s"
        )
    except OSError as exc:
        return ComponentHealth(
            name=name, status=HealthStatus.DOWN, detail=f"tcp connect failed: {exc.strerror or exc}"
        )

    writer.close()
    # Close-time races are not a health signal.
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return ComponentHealth(
        name=name,
        status=HealthStatus.OK,
        detail="tcp reachable",
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
