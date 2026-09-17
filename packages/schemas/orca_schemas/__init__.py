"""Shared ORCA data contracts.

Pydantic models here are the single source of truth. TypeScript types are generated
from them (``pnpm schemas:generate``) so the API and the web app cannot drift.

Phase 0 defines only the cross-cutting envelope: run identity, health and errors.
Domain contracts (ingest records, evidence, inter-agent messages, provenance nodes)
are added by the phases that own them.
"""

from orca_schemas.base import (
    RUN_ID_HEADER,
    ComponentHealth,
    HealthStatus,
    ProblemDetail,
    RunContext,
    ServiceHealth,
    new_run_id,
)

__all__ = [
    "RUN_ID_HEADER",
    "ComponentHealth",
    "HealthStatus",
    "ProblemDetail",
    "RunContext",
    "ServiceHealth",
    "new_run_id",
]
__version__ = "0.1.0"
