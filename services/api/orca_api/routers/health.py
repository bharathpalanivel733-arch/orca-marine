"""Liveness and readiness endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from orca_schemas import ComponentHealth, HealthStatus, ServiceHealth

from orca_api import __version__
from orca_api.config import Settings, get_settings
from orca_api.health import probe_tcp

router = APIRouter(tags=["health"])

SERVICE_NAME = "orca-api"


@router.get("/healthz", response_model=ServiceHealth, summary="Liveness")
async def healthz() -> ServiceHealth:
    """Is the process up? No dependencies are consulted."""
    return ServiceHealth(service=SERVICE_NAME, version=__version__, status=HealthStatus.OK)


@router.get("/readyz", response_model=ServiceHealth, summary="Readiness")
async def readyz() -> ServiceHealth:
    """Are the dev-stack dependencies reachable?

    Components are probed concurrently. A missing dependency downgrades the service
    rather than raising: reporting degraded state accurately is the behaviour ORCA
    claims end to end (ARCHITECTURE.md §7).
    """
    settings: Settings = get_settings()
    components: tuple[ComponentHealth, ...] = tuple(
        await asyncio.gather(
            probe_tcp("postgres", settings.database_url),
            probe_tcp("redis", settings.redis_url),
            probe_tcp("object-store", settings.s3_endpoint_url),
        )
    )
    return ServiceHealth.from_components(SERVICE_NAME, __version__, components)
