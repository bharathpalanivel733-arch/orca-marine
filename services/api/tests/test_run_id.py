"""Run-id propagation (PLAN.md Phase 0.5)."""

from __future__ import annotations

from orca_schemas import RUN_ID_HEADER


def test_run_id_is_minted_when_absent(client) -> None:
    response = client.get("/healthz")
    run_id = response.headers[RUN_ID_HEADER]
    assert run_id.startswith("orca_")


def test_inbound_run_id_is_preserved(client) -> None:
    """A run id crossing a service boundary must survive, or replay breaks."""
    response = client.get("/healthz", headers={RUN_ID_HEADER: "orca_fixed_run_id"})
    assert response.headers[RUN_ID_HEADER] == "orca_fixed_run_id"


def test_run_ids_differ_between_requests(client) -> None:
    first = client.get("/healthz").headers[RUN_ID_HEADER]
    second = client.get("/healthz").headers[RUN_ID_HEADER]
    assert first != second
