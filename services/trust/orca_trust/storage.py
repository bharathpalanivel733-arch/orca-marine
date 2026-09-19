"""Persistence for the provenance graph (PLAN.md Phase 6.4).

Writes the graph and its verdict under one ``run_id`` so a decision can be produced on
demand months later — for an audit, a post-incident review, or a jury asking "show me the
one from Tuesday".

Two query shapes are supported beyond simple retrieval, because both are asked in anger
rather than in curiosity:

* ``runs_using_payload`` — when a source is found to have published bad data, which
  decisions read it?
* ``runs_using_formula`` — when a kernel bug is found, which decisions used that version?

Writes are idempotent on ``run_id``: re-recording a run replaces its graph rather than
accumulating duplicates.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from orca_trust.provenance import ProvenanceGraph, ProvenanceNode, ProvenanceNodeKind
from orca_trust.verdict import TrustVerdict

UPSERT_RUN_SQL = """
INSERT INTO provenance.runs
    (run_id, session_id, turn_index, status, fingerprint, recorded_at, verdict)
VALUES (%(run_id)s, %(session_id)s, %(turn_index)s, %(status)s, %(fingerprint)s,
        %(recorded_at)s, %(verdict)s::jsonb)
ON CONFLICT (run_id) DO UPDATE SET
    session_id = EXCLUDED.session_id,
    turn_index = EXCLUDED.turn_index,
    status = EXCLUDED.status,
    fingerprint = EXCLUDED.fingerprint,
    recorded_at = EXCLUDED.recorded_at,
    verdict = EXCLUDED.verdict
"""

INSERT_NODE_SQL = """
INSERT INTO provenance.nodes (run_id, node_id, kind, label, occurred_at, attributes)
VALUES (%(run_id)s, %(node_id)s, %(kind)s, %(label)s, %(occurred_at)s, %(attributes)s::jsonb)
ON CONFLICT (run_id, node_id) DO UPDATE SET
    kind = EXCLUDED.kind, label = EXCLUDED.label,
    occurred_at = EXCLUDED.occurred_at, attributes = EXCLUDED.attributes
"""

INSERT_EDGE_SQL = """
INSERT INTO provenance.edges (run_id, source_id, target_id, relation)
VALUES (%(run_id)s, %(source_id)s, %(target_id)s, %(relation)s)
ON CONFLICT DO NOTHING
"""


class ProvenanceStore:
    """Reads and writes provenance graphs."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def ensure_schema(self, migration_sql: str) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(migration_sql)
        self._connection.commit()

    def record(
        self,
        graph: ProvenanceGraph,
        verdict: TrustVerdict,
        *,
        session_id: str | None = None,
        turn_index: int | None = None,
    ) -> str:
        """Persist a run. Returns its fingerprint.

        Replacing the nodes and edges rather than merging them matters: a re-recorded run
        that kept stale nodes would produce a graph describing a decision that never
        happened.
        """
        fingerprint = graph.fingerprint()
        with self._connection.cursor() as cursor:
            cursor.execute(
                UPSERT_RUN_SQL,
                {
                    "run_id": graph.run_id,
                    "session_id": session_id,
                    "turn_index": turn_index,
                    "status": verdict.status.value,
                    "fingerprint": fingerprint,
                    "recorded_at": graph.recorded_at,
                    "verdict": verdict.model_dump_json(),
                },
            )
            cursor.execute(
                "DELETE FROM provenance.nodes WHERE run_id = %(run_id)s",
                {"run_id": graph.run_id},
            )
            cursor.execute(
                "DELETE FROM provenance.edges WHERE run_id = %(run_id)s",
                {"run_id": graph.run_id},
            )
            for node in graph.nodes:
                cursor.execute(
                    INSERT_NODE_SQL,
                    {
                        "run_id": graph.run_id,
                        "node_id": node.node_id,
                        "kind": node.kind.value,
                        "label": node.label,
                        "occurred_at": node.occurred_at,
                        "attributes": json.dumps(node.attributes),
                    },
                )
            for edge in graph.edges:
                cursor.execute(
                    INSERT_EDGE_SQL,
                    {
                        "run_id": graph.run_id,
                        "source_id": edge.source_id,
                        "target_id": edge.target_id,
                        "relation": edge.relation,
                    },
                )
        self._connection.commit()
        return fingerprint

    def load(self, run_id: str) -> ProvenanceGraph | None:
        """Read a graph back by ``run_id``."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT recorded_at FROM provenance.runs WHERE run_id = %(run_id)s",
                {"run_id": run_id},
            )
            row = cursor.fetchone()
            if row is None:
                return None
            recorded_at: datetime = row[0]

            cursor.execute(
                "SELECT node_id, kind, label, occurred_at, attributes "
                "FROM provenance.nodes WHERE run_id = %(run_id)s ORDER BY node_id",
                {"run_id": run_id},
            )
            nodes = tuple(
                ProvenanceNode(
                    node_id=r[0],
                    kind=ProvenanceNodeKind(r[1]),
                    label=r[2],
                    occurred_at=r[3],
                    attributes=r[4] or {},
                )
                for r in cursor.fetchall()
            )

            cursor.execute(
                "SELECT source_id, target_id, relation FROM provenance.edges "
                "WHERE run_id = %(run_id)s ORDER BY source_id, target_id",
                {"run_id": run_id},
            )
            from orca_trust.provenance import ProvenanceEdge

            edges = tuple(
                ProvenanceEdge(source_id=r[0], target_id=r[1], relation=r[2])
                for r in cursor.fetchall()
            )

        return ProvenanceGraph(run_id=run_id, nodes=nodes, edges=edges, recorded_at=recorded_at)

    def load_verdict(self, run_id: str) -> TrustVerdict | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT verdict FROM provenance.runs WHERE run_id = %(run_id)s",
                {"run_id": run_id},
            )
            row = cursor.fetchone()
        if row is None:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return TrustVerdict.model_validate(payload)

    def runs_using_payload(self, sha256: str) -> tuple[str, ...]:
        """Every run that read a given archived payload."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT run_id FROM provenance.nodes "
                "WHERE kind = 'raw_payload' AND attributes ->> 'sha256' = %(sha)s "
                "ORDER BY run_id",
                {"sha": sha256},
            )
            return tuple(r[0] for r in cursor.fetchall())

    def runs_using_formula(self, formula_id: str, version: str | None = None) -> tuple[str, ...]:
        """Every run that used a formula, optionally pinned to a version."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT run_id FROM provenance.nodes "
                "WHERE kind = 'formula' AND attributes ->> 'formula_id' = %(fid)s "
                "AND (%(ver)s::text IS NULL OR attributes ->> 'formula_version' = %(ver)s::text) "
                "ORDER BY run_id",
                {"fid": formula_id, "ver": version},
            )
            return tuple(r[0] for r in cursor.fetchall())

    def abstentions_since(self, moment: datetime) -> tuple[str, ...]:
        """Runs that abstained, for reviewing how often ORCA refuses and why."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT run_id FROM provenance.runs WHERE status = 'abstain' "
                "AND recorded_at >= %(since)s ORDER BY recorded_at DESC",
                {"since": moment},
            )
            return tuple(r[0] for r in cursor.fetchall())
