"""Cross-cutting envelope contracts (PLAN.md Phase 0.1, 0.5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

RUN_ID_HEADER = "X-Run-Id"
"""Header carrying the run identifier across every service hop.

The same value keys the provenance graph and the deterministic replay store
(PLAN.md Phase 6.4), so it is established in Phase 0 and never regenerated
mid-request.
"""


def new_run_id() -> str:
    """Return a fresh run identifier.

    UUID4 hex with an ``orca_`` prefix: greppable in logs, safe in URLs and headers.
    """
    return f"orca_{uuid.uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class HealthStatus(str, Enum):
    """Service or dependency condition.

    ``degraded`` is a first-class state, not a soft failure: ORCA is designed to keep
    operating with missing or stale inputs and to say so (ARCHITECTURE.md §7).
    """

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class OrcaModel(BaseModel):
    """Base model: strict field validation, no silent extras."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RunContext(OrcaModel):
    """Identity of a single end-to-end run, propagated to every service and log line."""

    run_id: str = Field(
        default_factory=new_run_id, description="Unique id for this run."
    )
    parent_run_id: str | None = Field(
        default=None, description="Set when this run was spawned by another run."
    )
    service: str = Field(
        description="Service that created or received the run context."
    )
    started_at: datetime = Field(default_factory=_utc_now)


class ComponentHealth(OrcaModel):
    """Condition of one dependency (database, cache, object store, upstream feed)."""

    name: str
    status: HealthStatus
    detail: str | None = Field(
        default=None, description="Human-readable reason, required when not ok."
    )
    latency_ms: float | None = Field(default=None, ge=0)


class ServiceHealth(OrcaModel):
    """Aggregate health of one ORCA service."""

    service: str
    version: str
    status: HealthStatus
    checked_at: datetime = Field(default_factory=_utc_now)
    components: tuple[ComponentHealth, ...] = ()

    @classmethod
    def from_components(
        cls, service: str, version: str, components: tuple[ComponentHealth, ...]
    ) -> ServiceHealth:
        """Roll component conditions up into one service status.

        Any ``down`` dependency makes the service ``down``; any ``degraded`` one makes
        it ``degraded``. Deterministic, so health output is testable.
        """
        statuses = {c.status for c in components}
        if HealthStatus.DOWN in statuses:
            status = HealthStatus.DOWN
        elif HealthStatus.DEGRADED in statuses:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.OK
        return cls(
            service=service, version=version, status=status, components=components
        )


class ProblemDetail(OrcaModel):
    """Error payload (RFC 9457 shape) carrying the run id for support and replay."""

    type: str = Field(default="about:blank")
    title: str
    status: int = Field(ge=100, le=599)
    detail: str | None = None
    run_id: str | None = None
