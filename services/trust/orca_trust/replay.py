"""Deterministic replay by ``run_id`` (PLAN.md Phase 6.4).

Replay re-derives a past decision from the **archived bytes it originally read**, not by
re-querying the sources. The distinction is the entire point:

* *Re-running* a query tomorrow fetches a revised forecast. A different answer proves
  nothing, and an identical one proves nothing either.
* *Replaying* feeds the stored payloads back through the same formula versions. Any
  difference in output is therefore a difference in **code**, which is exactly what an
  auditor, a regression test, or a post-incident review needs to see.

Three things must line up for a replay to be sound, and each is checked separately so a
failure says which one broke:

1. the archived payloads are still present and hash to what the graph recorded
   (:class:`ReplayMismatch` — tampered or missing evidence);
2. the formula versions in use still match those recorded (a bumped version is a *valid*
   difference, and is reported as such rather than silently compared);
3. the recomputed outputs match the stored ones.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from orca_trust.provenance import ProvenanceGraph, ProvenanceNodeKind


class ReplayOutcome(StrEnum):
    """How a replay finished."""

    IDENTICAL = "identical"
    OUTPUT_DIFFERS = "output_differs"
    FORMULA_CHANGED = "formula_changed"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    EVIDENCE_TAMPERED = "evidence_tampered"


class ReplayMismatch(RuntimeError):
    """Archived evidence is missing or does not hash to its recorded value."""


@dataclass(frozen=True)
class ReplayReport:
    """The result of replaying one run."""

    run_id: str
    outcome: ReplayOutcome
    detail: str
    original_fingerprint: str
    replayed_fingerprint: str | None = None
    payloads_verified: int = 0
    differences: tuple[str, ...] = field(default_factory=tuple)

    @property
    def identical(self) -> bool:
        return self.outcome is ReplayOutcome.IDENTICAL


class PayloadArchiveReader:
    """Reads archived payloads by hash.

    A thin protocol over the Phase 1.10 archive so replay can be tested against an
    in-memory store and run against S3/MinIO unchanged.
    """

    def __init__(self, payloads: Mapping[str, bytes]) -> None:
        self._payloads = dict(payloads)

    def read(self, sha256: str) -> bytes:
        try:
            return self._payloads[sha256]
        except KeyError:
            msg = f"archived payload {sha256[:12]}... is not available"
            raise ReplayMismatch(msg) from None

    def has(self, sha256: str) -> bool:
        return sha256 in self._payloads


def verify_payloads(graph: ProvenanceGraph, archive: PayloadArchiveReader) -> int:
    """Check every recorded payload is present and unmodified.

    Content addressing makes this exact: if the stored bytes no longer hash to the
    recorded digest, the evidence changed after the decision was made, and replaying it
    would compare against something the original run never saw.
    """
    verified = 0
    for node in graph.nodes_of(ProvenanceNodeKind.RAW_PAYLOAD):
        recorded = node.attributes["sha256"]
        if not archive.has(recorded):
            msg = f"payload {recorded[:12]}... referenced by {node.node_id} is missing"
            raise ReplayMismatch(msg)
        actual = hashlib.sha256(archive.read(recorded)).hexdigest()
        if actual != recorded:
            msg = (
                f"payload for {node.node_id} hashes to {actual[:12]}... but the run "
                f"recorded {recorded[:12]}...; the archived evidence has changed"
            )
            raise ReplayMismatch(msg)
        verified += 1
    return verified


def replay_run(
    *,
    graph: ProvenanceGraph,
    archive: PayloadArchiveReader,
    recompute: Callable[[dict[str, bytes]], ProvenanceGraph],
    current_formula_versions: Mapping[str, str] | None = None,
    replayed_at: datetime | None = None,
) -> ReplayReport:
    """Replay a run and report whether it reproduced exactly.

    ``recompute`` receives the archived payloads keyed by hash and must return the graph
    the re-derivation produced. It is injected rather than imported so replay does not
    depend on the orchestrator, and so a test can exercise the comparison logic directly.
    """
    original_fingerprint = graph.fingerprint()

    try:
        verified = verify_payloads(graph, archive)
    except ReplayMismatch as mismatch:
        outcome = (
            ReplayOutcome.EVIDENCE_UNAVAILABLE
            if "missing" in str(mismatch)
            else ReplayOutcome.EVIDENCE_TAMPERED
        )
        return ReplayReport(
            run_id=graph.run_id,
            outcome=outcome,
            detail=str(mismatch),
            original_fingerprint=original_fingerprint,
        )

    # A changed formula version is a legitimate reason for a different answer, so it is
    # reported distinctly rather than surfacing as a mysterious output difference.
    if current_formula_versions is not None:
        changed = [
            f"{formula_id}: recorded {recorded_version}, now "
            f"{current_formula_versions.get(formula_id, 'absent')}"
            for formula_id, recorded_version in graph.formulas()
            if current_formula_versions.get(formula_id) != recorded_version
        ]
        if changed:
            return ReplayReport(
                run_id=graph.run_id,
                outcome=ReplayOutcome.FORMULA_CHANGED,
                detail=(
                    "formula versions have changed since this run; a difference in output "
                    "is expected and is not evidence of tampering"
                ),
                original_fingerprint=original_fingerprint,
                payloads_verified=verified,
                differences=tuple(changed),
            )

    payloads = {
        node.attributes["sha256"]: archive.read(node.attributes["sha256"])
        for node in graph.nodes_of(ProvenanceNodeKind.RAW_PAYLOAD)
    }
    replayed = recompute(payloads)
    replayed_fingerprint = replayed.fingerprint()

    if replayed_fingerprint == original_fingerprint:
        return ReplayReport(
            run_id=graph.run_id,
            outcome=ReplayOutcome.IDENTICAL,
            detail=(
                f"replay reproduced the run exactly from {verified} archived payload(s); "
                "fingerprints match"
            ),
            original_fingerprint=original_fingerprint,
            replayed_fingerprint=replayed_fingerprint,
            payloads_verified=verified,
        )

    return ReplayReport(
        run_id=graph.run_id,
        outcome=ReplayOutcome.OUTPUT_DIFFERS,
        detail="replay produced a different result from identical archived inputs",
        original_fingerprint=original_fingerprint,
        replayed_fingerprint=replayed_fingerprint,
        payloads_verified=verified,
        differences=_diff_outputs(graph, replayed),
    )


def _diff_outputs(original: ProvenanceGraph, replayed: ProvenanceGraph) -> tuple[str, ...]:
    """Name the outputs that differ, so a failure points at the culprit."""

    def outputs(graph: ProvenanceGraph) -> dict[str, Any]:
        return {
            node.node_id: node.attributes.get("value")
            for node in graph.nodes_of(ProvenanceNodeKind.OUTPUT)
        }

    before, after = outputs(original), outputs(replayed)
    differences = [
        f"{node_id}: was {before[node_id]!r}, replayed {after[node_id]!r}"
        for node_id in sorted(set(before) | set(after))
        if before.get(node_id) != after.get(node_id)
    ]
    return tuple(differences)
