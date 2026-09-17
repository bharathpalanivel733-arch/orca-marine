"""Structured logging, run-id propagation and tracing (PLAN.md Phase 0.5).

The ``run_id`` established here is the same identifier the provenance graph and the
deterministic replay store key on (PLAN.md Phase 6.4). It is created once per request,
carried in a context variable so every log line and span picks it up without being
passed around, and echoed back on the response so a user-visible answer can always be
traced to the run that produced it.

OpenTelemetry is optional: when ``OTEL_ENABLED`` is false the tracing helpers are
no-ops, so the service runs with no collector present.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from orca_schemas import RUN_ID_HEADER, new_run_id

from orca_api.config import LogFormat, Settings

_run_id: ContextVar[str | None] = ContextVar("orca_run_id", default=None)


def get_run_id() -> str | None:
    """Return the run id bound to the current context, if any."""
    return _run_id.get()


def bind_run_id(run_id: str) -> None:
    """Bind a run id to this context and to the structlog log context."""
    _run_id.set(run_id)
    structlog.contextvars.bind_contextvars(run_id=run_id)


def configure_logging(settings: Settings) -> None:
    """Configure structlog. JSON in every environment except explicit console mode."""
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.orca_log_format is LogFormat.JSON
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(settings.orca_log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)


def setup_tracing(app: FastAPI, settings: Settings) -> bool:
    """Install OpenTelemetry instrumentation when enabled and importable.

    Returns whether tracing was actually installed, so health output can state the
    real condition instead of assuming.
    """
    if not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        get_logger(__name__).warning(
            "otel_requested_but_unavailable",
            hint="install the 'otel' extra: pip install -e 'services/api[otel]'",
        )
        return False

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    return True


@contextmanager
def span_for_node(node_name: str, **attributes: Any) -> Iterator[None]:
    """Trace one task-DAG node (PLAN.md Phase 0.5).

    A no-op when OpenTelemetry is absent or disabled, so agent code can wrap every
    node unconditionally. The node's run id is attached automatically.
    """
    try:
        from opentelemetry import trace
    except ImportError:
        yield
        return

    tracer = trace.get_tracer("orca")
    with tracer.start_as_current_span(f"dag.node.{node_name}") as span:
        run_id = get_run_id()
        if run_id:
            span.set_attribute("orca.run_id", run_id)
        for key, value in attributes.items():
            span.set_attribute(f"orca.{key}", value)
        yield


def install_run_id_middleware(app: FastAPI) -> None:
    """Accept an inbound run id or mint one, bind it, and echo it on the response."""

    @app.middleware("http")
    async def _run_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        structlog.contextvars.clear_contextvars()
        run_id = request.headers.get(RUN_ID_HEADER) or new_run_id()
        bind_run_id(run_id)
        request.state.run_id = run_id

        log = get_logger("orca_api.request")
        log.info("request_started", method=request.method, path=request.url.path)
        response = await call_next(request)
        log.info(
            "request_finished",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
        )
        response.headers[RUN_ID_HEADER] = run_id
        return response
