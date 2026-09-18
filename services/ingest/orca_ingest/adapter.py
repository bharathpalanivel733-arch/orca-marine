"""The source adapter interface (PLAN.md Phase 1.1).

One uniform ``fetch(request)`` signature over every marine data source, so the planner
can swap INCOIS for CMEMS for Open-Meteo without the calling code changing shape
(DEPLOYMENT.md §4, "degrade gracefully").

An adapter is responsible for exactly three things:

1. declaring what it can serve, via its :class:`~orca_schemas.SourceDescriptor`;
2. translating upstream naming and units into the canonical
   :class:`~orca_schemas.ObservationRecord` contract;
3. reporting what it could not do, via ``warnings`` and ``missing_variables``, rather
   than silently returning less than was asked for.

What an adapter must never do is invent, extrapolate or smooth a value. A gap is
reported as a gap; the trust layer decides what to make of it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from orca_schemas import (
    ArchiveRef,
    BoundingBox,
    Cadence,
    MarineVariable,
    ObservationRecord,
    OrcaModel,
    SourceDescriptor,
    TimeWindow,
)
from pydantic import Field


class FetchRequest(OrcaModel):
    """A uniform ask: these variables, in this box, over this window."""

    bbox: BoundingBox
    time_window: TimeWindow
    variables: frozenset[MarineVariable] = Field(min_length=1)


class FetchResult(OrcaModel):
    """What an adapter returned, including what it could not return.

    ``missing_variables`` and ``warnings`` are part of the contract, not diagnostics:
    the evidence-sufficiency gate (PLAN.md Phase 6.3) needs to know an ask went
    unanswered in order to abstain instead of answering from thin air.
    """

    source_id: str
    records: tuple[ObservationRecord, ...] = ()
    retrieved_at: datetime
    missing_variables: frozenset[MarineVariable] = frozenset()
    warnings: tuple[str, ...] = ()
    raw_refs: tuple[ArchiveRef, ...] = Field(
        default=(),
        description="Archived upstream payloads behind these records (PLAN.md Phase 1.10).",
    )

    def records_for(self, variable: MarineVariable) -> tuple[ObservationRecord, ...]:
        """Records for one variable, in the order the adapter returned them."""
        return tuple(r for r in self.records if r.variable is variable)

    def stale_records(self, cadence: Cadence, now: datetime) -> tuple[ObservationRecord, ...]:
        """Records too old for the given cadence."""
        return tuple(r for r in self.records if r.is_stale(cadence, now))

    @property
    def is_empty(self) -> bool:
        """Whether nothing at all came back."""
        return not self.records


class SourceUnavailableError(RuntimeError):
    """Raised when a source cannot be reached or refuses the request.

    Distinct from an empty result: 'the sea has no waves there' and 'INCOIS did not
    answer' must not collapse into the same thing, because the degradation chain reacts
    to the second and not the first.
    """

    def __init__(self, source_id: str, reason: str) -> None:
        self.source_id = source_id
        self.reason = reason
        super().__init__(f"{source_id}: {reason}")


class SourceAdapter(ABC):
    """Base class for every marine data source adapter."""

    @property
    @abstractmethod
    def descriptor(self) -> SourceDescriptor:
        """Registry metadata for this source."""

    @abstractmethod
    async def fetch(self, request: FetchRequest) -> FetchResult:
        """Retrieve records for the request.

        Implementations must emit canonical units, set provenance fields from the
        upstream response (never from the local clock, for ``issued_time``), and raise
        :class:`SourceUnavailableError` when the source itself fails. Partial success is
        expressed through ``missing_variables``, not through an exception.
        """

    def unsupported_variables(self, request: FetchRequest) -> frozenset[MarineVariable]:
        """Variables in the request this source does not publish."""
        return frozenset(v for v in request.variables if not self.descriptor.serves(v))

    def covers(self, request: FetchRequest) -> bool:
        """Whether this source's coverage overlaps the requested box at all."""
        return self.descriptor.coverage.intersects(request.bbox)

    def supports(self, request: FetchRequest) -> bool:
        """Whether this source can serve the whole request.

        Deterministic capability check used to choose a source before spending a call
        on it. It answers 'could this source serve this', not 'is the source up'.
        """
        return self.covers(request) and not self.unsupported_variables(request)
