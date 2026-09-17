"""Contract tests for the shared envelope models."""

from __future__ import annotations

import pytest
from orca_schemas import (
    ComponentHealth,
    HealthStatus,
    ProblemDetail,
    RunContext,
    ServiceHealth,
    new_run_id,
)
from pydantic import ValidationError


def test_run_ids_are_unique_and_prefixed() -> None:
    ids = {new_run_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("orca_") for i in ids)


def test_run_context_defaults_are_populated() -> None:
    ctx = RunContext(service="orca-api")
    assert ctx.run_id.startswith("orca_")
    assert ctx.parent_run_id is None
    assert ctx.started_at.tzinfo is not None


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RunContext(service="orca-api", sevice="typo")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("component_statuses", "expected"),
    [
        ((), HealthStatus.OK),
        ((HealthStatus.OK, HealthStatus.OK), HealthStatus.OK),
        ((HealthStatus.OK, HealthStatus.DEGRADED), HealthStatus.DEGRADED),
        ((HealthStatus.DEGRADED, HealthStatus.DOWN), HealthStatus.DOWN),
        ((HealthStatus.OK, HealthStatus.DOWN), HealthStatus.DOWN),
    ],
)
def test_health_rollup_is_worst_case(
    component_statuses: tuple[HealthStatus, ...], expected: HealthStatus
) -> None:
    components = tuple(
        ComponentHealth(name=f"dep{i}", status=s) for i, s in enumerate(component_statuses)
    )
    health = ServiceHealth.from_components("orca-api", "0.1.0", components)
    assert health.status is expected


def test_problem_detail_validates_status_range() -> None:
    assert ProblemDetail(title="Bad Request", status=400).status == 400
    with pytest.raises(ValidationError):
        ProblemDetail(title="nonsense", status=42)
