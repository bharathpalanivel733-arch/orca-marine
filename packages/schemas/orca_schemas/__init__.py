"""Shared ORCA data contracts.

Pydantic models here are the single source of truth. TypeScript types are generated
from them (``pnpm schemas:generate``) so the API and the web app cannot drift.

``base`` holds the cross-cutting envelope (run identity, health, errors); ``ingest``
holds the common observation record and source metadata that every data adapter speaks
(PLAN.md Phase 1.1). Later domain contracts (evidence, inter-agent messages, provenance
nodes) are added by the phases that own them.
"""

from orca_schemas.base import (
    RUN_ID_HEADER,
    ComponentHealth,
    HealthStatus,
    OrcaModel,
    ProblemDetail,
    RunContext,
    ServiceHealth,
    new_run_id,
)
from orca_schemas.ingest import (
    CANONICAL_UNITS,
    ArchiveRef,
    AttemptOutcome,
    BoundingBox,
    Cadence,
    DataQuality,
    MarineVariable,
    MeasurementKind,
    ObservationRecord,
    SourceAttempt,
    SourceDescriptor,
    TimeWindow,
)

__all__ = [
    "CANONICAL_UNITS",
    "RUN_ID_HEADER",
    "ArchiveRef",
    "AttemptOutcome",
    "BoundingBox",
    "Cadence",
    "ComponentHealth",
    "DataQuality",
    "HealthStatus",
    "MarineVariable",
    "MeasurementKind",
    "ObservationRecord",
    "OrcaModel",
    "ProblemDetail",
    "RunContext",
    "ServiceHealth",
    "SourceAttempt",
    "SourceDescriptor",
    "TimeWindow",
    "new_run_id",
]
__version__ = "0.1.0"
