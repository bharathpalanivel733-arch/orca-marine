"""Provenance persistence and replay from the database (PLAN.md Phase 6.4).

``-m stack``: runs against the real PostgreSQL from ``infra/docker-compose.yml``.

Persisting the graph is what makes replay possible months later rather than only within a
process. The round-trip test therefore asserts the *fingerprint* survives storage — if
loading a graph back produced a different fingerprint, every stored decision would be
unreplayable and the trust claim would be empty.
"""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path

import pytest

from orca_trust import (
    PayloadArchiveReader,
    ProvenanceStore,
    ReplayOutcome,
    TrustVerdict,
    VerdictStatus,
    check_freshness,
    check_source_validity,
    replay_run,
)

from .test_trust_layer import NOW, build_graph, item

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://orca:orca@localhost:5432/orca")
MIGRATIONS = Path(__file__).resolve().parents[3] / "infra" / "db" / "migrations"

PAYLOAD = b'{"vhm0": 1.4, "issued": "2026-09-19T11:00:00Z"}'
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def store():
    psycopg = pytest.importorskip("psycopg")
    try:
        connection = psycopg.connect(DATABASE_URL, connect_timeout=5)
    except psycopg.OperationalError as exc:
        pytest.skip(f"dev database not reachable ({exc}); run `pnpm db:up`")

    store = ProvenanceStore(connection)
    store.ensure_schema((MIGRATIONS / "0005_provenance.sql").read_text(encoding="utf-8"))
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM provenance.runs WHERE run_id LIKE 'test-%'")
    connection.commit()

    yield store

    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM provenance.runs WHERE run_id LIKE 'test-%'")
    connection.commit()
    connection.close()


def answering_verdict() -> TrustVerdict:
    return TrustVerdict(
        status=VerdictStatus.ANSWER,
        checks=(check_source_validity([item()]), check_freshness([item()], now=NOW)),
        evidence_count=2,
        evaluated_at=NOW,
    )


def abstaining_verdict() -> TrustVerdict:
    return TrustVerdict(
        status=VerdictStatus.ABSTAIN,
        checks=(check_freshness([item(age_hours=15)], now=NOW),),
        abstain_reasons=("nearest reliable data is 15.0 h old",),
        remedy="wait for the next forecast issue",
        evaluated_at=NOW,
    )


@pytest.mark.stack
class TestProvenancePersistence:
    def test_a_graph_round_trips_with_its_fingerprint_intact(self, store) -> None:
        """If storage changed the fingerprint, every stored decision would be unreplayable."""
        graph = build_graph("test-run-1", wave_value=72.0, payload=PAYLOAD)
        original_fingerprint = graph.fingerprint()

        store.record(graph, answering_verdict(), session_id="s1", turn_index=0)
        loaded = store.load("test-run-1")

        assert loaded is not None
        assert loaded.fingerprint() == original_fingerprint
        assert loaded.payload_hashes() == (DIGEST,)
        assert loaded.formulas() == (("orca.safety.boat_relative_hazard", "1.0.0"),)

    def test_the_verdict_round_trips_including_its_checks(self, store) -> None:
        graph = build_graph("test-run-2", wave_value=72.0, payload=PAYLOAD)
        store.record(graph, abstaining_verdict())

        loaded = store.load_verdict("test-run-2")

        assert loaded is not None
        assert loaded.status is VerdictStatus.ABSTAIN
        assert loaded.abstain_reasons == ("nearest reliable data is 15.0 h old",)
        assert len(loaded.checks) == 1

    def test_an_unknown_run_returns_none(self, store) -> None:
        assert store.load("test-does-not-exist") is None

    def test_re_recording_replaces_rather_than_accumulates(self, store) -> None:
        """A stale node left behind would describe a decision that never happened."""
        store.record(
            build_graph("test-run-3", wave_value=72.0, payload=PAYLOAD), answering_verdict()
        )
        store.record(
            build_graph("test-run-3", wave_value=41.0, payload=PAYLOAD), answering_verdict()
        )

        loaded = store.load("test-run-3")

        assert loaded is not None
        assert len(loaded.nodes) == 5
        output = next(n for n in loaded.nodes if n.node_id == "out:safety_score")
        assert output.attributes["value"] == 41.0

    def test_runs_can_be_found_by_the_payload_they_read(self, store) -> None:
        """Asked in anger: a source published bad data — which decisions used it?"""
        store.record(
            build_graph("test-run-4", wave_value=72.0, payload=PAYLOAD), answering_verdict()
        )
        store.record(
            build_graph("test-run-5", wave_value=68.0, payload=PAYLOAD), answering_verdict()
        )
        store.record(
            build_graph("test-run-6", wave_value=50.0, payload=b'{"vhm0": 3.9}'),
            answering_verdict(),
        )

        affected = store.runs_using_payload(DIGEST)

        assert set(affected) == {"test-run-4", "test-run-5"}

    def test_runs_can_be_found_by_formula_version(self, store) -> None:
        """Asked in anger: a kernel bug — which decisions used that version?"""
        store.record(
            build_graph("test-run-7", wave_value=72.0, payload=PAYLOAD), answering_verdict()
        )

        assert "test-run-7" in store.runs_using_formula("orca.safety.boat_relative_hazard")
        assert "test-run-7" in store.runs_using_formula("orca.safety.boat_relative_hazard", "1.0.0")
        assert store.runs_using_formula("orca.safety.boat_relative_hazard", "9.9.9") == ()

    def test_abstentions_are_queryable(self, store) -> None:
        store.record(
            build_graph("test-run-8", wave_value=0.0, payload=PAYLOAD), abstaining_verdict()
        )
        store.record(
            build_graph("test-run-9", wave_value=72.0, payload=PAYLOAD), answering_verdict()
        )

        abstained = store.abstentions_since(NOW - timedelta(days=1))

        assert "test-run-8" in abstained
        assert "test-run-9" not in abstained


@pytest.mark.stack
class TestReplayFromTheDatabase:
    def test_a_stored_run_replays_identically(self, store) -> None:
        """End to end: persist, load months later, re-derive from archived bytes."""
        graph = build_graph("test-run-10", wave_value=72.0, payload=PAYLOAD)
        store.record(graph, answering_verdict())

        loaded = store.load("test-run-10")
        assert loaded is not None

        report = replay_run(
            graph=loaded,
            archive=PayloadArchiveReader({DIGEST: PAYLOAD}),
            recompute=lambda payloads: build_graph(
                "test-run-10", wave_value=72.0, payload=payloads[DIGEST]
            ),
        )

        assert report.identical
        assert report.outcome is ReplayOutcome.IDENTICAL

    def test_a_stored_run_detects_a_changed_result(self, store) -> None:
        graph = build_graph("test-run-11", wave_value=72.0, payload=PAYLOAD)
        store.record(graph, answering_verdict())
        loaded = store.load("test-run-11")
        assert loaded is not None

        report = replay_run(
            graph=loaded,
            archive=PayloadArchiveReader({DIGEST: PAYLOAD}),
            recompute=lambda payloads: build_graph(
                "test-run-11", wave_value=33.0, payload=payloads[DIGEST]
            ),
        )

        assert report.outcome is ReplayOutcome.OUTPUT_DIFFERS
