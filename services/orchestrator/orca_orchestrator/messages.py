"""Inter-agent messages and UI-streamable events (PLAN.md Phase 5.4, 5.8).

The message schema is the one ARCHITECTURE.md §5 specifies:
``{task_id, agent, status, inputs, evidence[], result, confidence, sources[],
issued_time, cost_used}``. Every node returns one, so the trace is uniform and the
provenance panel has a single shape to render.

Events are the same information as it happens rather than at the end. The demo turns on
the DAG being *visible* — nodes lighting up in parallel, evidence arriving, a fallback
firing — and that only works if the engine emits progress rather than a final blob.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from orca_schemas import OrcaModel
from pydantic import Field, field_validator

from orca_orchestrator.plan import AgentNode, Tool


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must be timezone-aware"
        raise ValueError(msg)
    return value


class TaskStatus(StrEnum):
    """Outcome of one node."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    ABSTAINED = "abstained"


class AgentMessage(OrcaModel):
    """One node's result, in the schema ARCHITECTURE.md §5 specifies."""

    task_id: str
    agent: AgentNode
    status: TaskStatus
    inputs: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[str, ...] = Field(
        default=(), description="Provenance ids of evidence used, not the evidence itself."
    )
    result: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    sources: tuple[str, ...] = ()
    issued_time: datetime
    cost_used: dict[str, float] = Field(default_factory=dict)
    tools_called: tuple[Tool, ...] = ()
    error: str | None = None

    _aware = field_validator("issued_time")(_require_aware)

    @property
    def ok(self) -> bool:
        return self.status is TaskStatus.SUCCEEDED

    @property
    def failed(self) -> bool:
        return self.status is TaskStatus.FAILED


class EventKind(StrEnum):
    """Streamable progress events (PLAN.md Phase 5.8)."""

    PLAN_READY = "plan_ready"
    PLAN_PRUNED = "plan_pruned"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    EVIDENCE_ARRIVED = "evidence_arrived"
    REPLANNED = "replanned"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RUN_FINISHED = "run_finished"


class RunEvent(OrcaModel):
    """One progress event, safe to send straight to a UI."""

    kind: EventKind
    at: datetime
    node: AgentNode | None = None
    detail: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    _aware = field_validator("at")(_require_aware)


class ToolFailure(RuntimeError):
    """A tool call failed in a way the engine should replan around.

    Distinct from a programming error: this is an upstream refusing or timing out, which
    is normal operation for a system sitting on public marine feeds. Raising a distinct
    type is what lets the engine tell "the data source is down" from "our code is wrong" —
    the first deserves a fallback, the second deserves a crash.
    """

    def __init__(self, node: AgentNode, reason: str) -> None:
        self.node = node
        self.reason = reason
        super().__init__(f"{node.value}: {reason}")
