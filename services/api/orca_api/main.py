"""ORCA API application factory (PLAN.md Phase 0.1).

Phase 0 wires configuration, structured logging, run-id propagation, optional tracing
and health endpoints. No marine data source, agent or LLM provider is called here —
those arrive with the phases that own them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from orca_api import __version__
from orca_api.config import get_settings, load_model_routing
from orca_api.observability import (
    configure_logging,
    get_logger,
    install_run_id_middleware,
    setup_tracing,
)
from orca_api.routers import health, speech


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("orca_api.startup")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Fail fast on a broken routing table rather than mid-query (PLAN.md §1.1).
        routing = load_model_routing(settings.models_config_path)
        app.state.model_routing = routing
        log.info(
            "service_started",
            service="orca-api",
            version=__version__,
            env=settings.orca_env,
            model_roles=sorted(routing.roles),
            tracing_enabled=app.state.tracing_enabled,
        )
        yield
        log.info("service_stopped", service="orca-api")

    app = FastAPI(
        title="ORCA API",
        version=__version__,
        summary="Agentic reasoning and trust layer over INCOIS / IMD / ISRO marine feeds.",
        lifespan=lifespan,
    )
    install_run_id_middleware(app)
    app.state.tracing_enabled = setup_tracing(app, settings)
    app.include_router(health.router)
    app.include_router(speech.router)
    return app


app = create_app()
