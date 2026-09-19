"""The provenance graph (PLAN.md Phase 6.4, gap M11).

Every decision is persisted as an auditable chain:

    dataset → raw payload hash → agent → formula(+version) → output

This is the concrete design that replaces the "7-point integrity check" language
(METHODS.md §2). It is not a log: it is a graph keyed by ``run_id`` from which the whole
decision can be re-derived, which is what makes the claim "deterministically replayable"
checkable rather than rhetorical.

The **raw payload hash** is what distinguishes replay from re-running. Re-running a query
tomorrow fetches a revised forecast and produces a different answer, which proves nothing.
Replaying reads the archived bytes that the original decision actually saw
(``ArchiveRef.sha256``, Phase 1.10), so any difference in output is a difference in *code*,
not in the weather.

Node kinds are ordered dataset → payload → agent → formula → output, and edges may only
run forward along that order. A cycle in a provenance graph would mean an output
influencing its own inputs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from orca_schemas import OrcaModel
from pydantic import Field, field_validator, model_validator


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must be timezone-aware"
        raise ValueError(msg)
    return value


class ProvenanceNodeKind(StrEnum):
    """The stages of the chain, in the order they may be linked."""

    DATASET = "dataset"
    RAW_PAYLOAD = "raw_payload"
    AGENT = "agent"
    FORMULA = "formula"
    OUTPUT = "output"


_ORDER: dict[ProvenanceNodeKind, int] = {
    ProvenanceNodeKind.DATASET: 0,
    ProvenanceNodeKind.RAW_PAYLOAD: 1,
    ProvenanceNodeKind.AGENT: 2,
    ProvenanceNodeKind.FORMULA: 3,
    ProvenanceNodeKind.OUTPUT: 4,
}


class ProvenanceNode(OrcaModel):
    """One step in the chain."""

    node_id: str = Field(min_length=1)
    kind: ProvenanceNodeKind
    label: str = Field(min_length=1)
    occurred_at: datetime
    # Kind-specific detail: dataset id, sha256, agent name, formula id + version, value.
    attributes: dict[str, Any] = Field(default_factory=dict)

    _aware = field_validator("occurred_at")(_require_aware)

    @model_validator(mode="after")
    def _required_attributes_present(self) -> Self:
        required = {
            ProvenanceNodeKind.RAW_PAYLOAD: ("sha256",),
            ProvenanceNodeKind.FORMULA: ("formula_id", "formula_version"),
        }.get(self.kind, ())
        missing = [key for key in required if key not in self.attributes]
        if missing:
            msg = f"{self.kind} node requires attribute(s): {', '.join(missing)}"
            raise ValueError(msg)
        return self


class ProvenanceEdge(OrcaModel):
    """A forward link between two stages."""

    source_id: str
    target_id: str
    relation: str = Field(default="derives", min_length=1)


class ProvenanceGraph(OrcaModel):
    """The full chain for one run, keyed by ``run_id``."""

    run_id: str = Field(min_length=1)
    nodes: tuple[ProvenanceNode, ...]
    edges: tuple[ProvenanceEdge, ...] = ()
    recorded_at: datetime

    _aware = field_validator("recorded_at")(_require_aware)

    @model_validator(mode="after")
    def _edges_are_well_formed_and_forward(self) -> Self:
        by_id = {n.node_id: n for n in self.nodes}
        if len(by_id) != len(self.nodes):
            msg = "node_id must be unique within a run"
            raise ValueError(msg)

        for edge in self.edges:
            for end in (edge.source_id, edge.target_id):
                if end not in by_id:
                    msg = f"edge references unknown node {end!r}"
                    raise ValueError(msg)
            source, target = by_id[edge.source_id], by_id[edge.target_id]
            if _ORDER[target.kind] < _ORDER[source.kind]:
                msg = (
                    f"provenance edge runs backwards: {source.kind} -> {target.kind}. "
                    "An output cannot influence its own inputs."
                )
                raise ValueError(msg)
        return self

    def nodes_of(self, kind: ProvenanceNodeKind) -> tuple[ProvenanceNode, ...]:
        return tuple(n for n in self.nodes if n.kind is kind)

    def payload_hashes(self) -> tuple[str, ...]:
        """Every archived payload this run read, in sorted order."""
        return tuple(
            sorted(n.attributes["sha256"] for n in self.nodes_of(ProvenanceNodeKind.RAW_PAYLOAD))
        )

    def formulas(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                (n.attributes["formula_id"], n.attributes["formula_version"])
                for n in self.nodes_of(ProvenanceNodeKind.FORMULA)
            )
        )

    def chain_for(self, output_id: str) -> tuple[ProvenanceNode, ...]:
        """Walk back from an output to every node that contributed to it.

        This is what the provenance panel renders when a user asks "where did this number
        come from" — and what an auditor walks when challenging one.
        """
        by_id = {n.node_id: n for n in self.nodes}
        incoming: dict[str, list[str]] = {}
        for edge in self.edges:
            incoming.setdefault(edge.target_id, []).append(edge.source_id)

        seen: list[str] = []
        stack = [output_id]
        while stack:
            current = stack.pop()
            if current in seen or current not in by_id:
                continue
            seen.append(current)
            stack.extend(incoming.get(current, ()))

        return tuple(sorted((by_id[i] for i in seen), key=lambda n: (_ORDER[n.kind], n.node_id)))

    def fingerprint(self) -> str:
        """Stable hash of the graph's structure and content.

        Excludes ``recorded_at`` and node timestamps: replaying a run records new wall
        clock times, and a fingerprint that changed because of that would be useless for
        proving the *decision* was reproduced.
        """
        payload = {
            "nodes": sorted(
                (
                    {
                        "node_id": n.node_id,
                        "kind": n.kind.value,
                        "label": n.label,
                        "attributes": n.attributes,
                    }
                    for n in self.nodes
                ),
                key=lambda n: str(n["node_id"]),
            ),
            "edges": sorted(
                ({"s": e.source_id, "t": e.target_id, "r": e.relation} for e in self.edges),
                key=lambda e: (str(e["s"]), str(e["t"])),
            ),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ProvenanceBuilder:
    """Accumulates a graph while a run executes."""

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._nodes: list[ProvenanceNode] = []
        self._edges: list[ProvenanceEdge] = []

    def add(
        self,
        *,
        node_id: str,
        kind: ProvenanceNodeKind,
        label: str,
        occurred_at: datetime,
        attributes: dict[str, Any] | None = None,
        derives_from: str | None = None,
    ) -> ProvenanceNode:
        """Append a node, optionally linking it to its predecessor."""
        node = ProvenanceNode(
            node_id=node_id,
            kind=kind,
            label=label,
            occurred_at=occurred_at,
            attributes=attributes or {},
        )
        self._nodes.append(node)
        if derives_from is not None:
            self._edges.append(ProvenanceEdge(source_id=derives_from, target_id=node_id))
        return node

    def link(self, source_id: str, target_id: str, *, relation: str = "derives") -> None:
        self._edges.append(
            ProvenanceEdge(source_id=source_id, target_id=target_id, relation=relation)
        )

    def build(self, *, recorded_at: datetime) -> ProvenanceGraph:
        return ProvenanceGraph(
            run_id=self._run_id,
            nodes=tuple(self._nodes),
            edges=tuple(self._edges),
            recorded_at=recorded_at,
        )
