"""Source registry and the explicit degradation policy (PLAN.md Phase 1.11).

DEPLOYMENT.md §4 states the fallback chains as prose — INCOIS SST -> NOAA_AVHRR_AMSR ->
CMEMS thetao; INCOIS waves -> CMEMS VHM0 -> Open-Meteo. This module is that prose made
executable, with two properties the prose alone does not give:

* **Every attempt is recorded, not just the winner.** A decision that used Open-Meteo
  must be able to show that INCOIS was tried and rejected as stale. That trail is what
  the provenance panel renders and what the verifier reasons over.
* **Stale data does not win by default.** A source whose records have passed their
  cadence deadline is treated as a failed source and the chain moves on. If every source
  is stale, the result is empty and says so — ORCA abstains rather than quietly serving
  last week's forecast as tomorrow's.

The ordering is deterministic: ``authority_rank`` first (1 = authoritative Indian
agency), then source id. No model decides the order.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from datetime import datetime

from orca_schemas import (
    ArchiveRef,
    AttemptOutcome,
    MarineVariable,
    ObservationRecord,
    OrcaModel,
    SourceAttempt,
)

from orca_ingest.adapter import FetchRequest, SourceAdapter, SourceUnavailableError


class ResolvedFetch(OrcaModel):
    """The outcome of a fetch across the whole fallback chain."""

    records: tuple[ObservationRecord, ...] = ()
    attempts: tuple[SourceAttempt, ...] = ()
    used_sources: tuple[str, ...] = ()
    missing_variables: frozenset[MarineVariable] = frozenset()
    archive_refs: tuple[ArchiveRef, ...] = ()

    @property
    def degraded(self) -> bool:
        """True when any source was tried and rejected before one succeeded."""
        return any(a.outcome is not AttemptOutcome.SUCCESS for a in self.attempts)

    @property
    def is_empty(self) -> bool:
        return not self.records

    def attempts_for(self, outcome: AttemptOutcome) -> tuple[SourceAttempt, ...]:
        return tuple(a for a in self.attempts if a.outcome is outcome)

    def explain(self) -> str:
        """One-line provenance summary, suitable for logs and the provenance panel."""
        parts = [
            f"{a.source_id}={a.outcome}" + (f"({a.reason})" if a.reason else "")
            for a in self.attempts
        ]
        return " -> ".join(parts) if parts else "no sources attempted"


class SourceRegistry:
    """Adapters available to serve a request.

    Phase 3.1 extends this into the planner-facing tool/dataset registry with richer
    capability metadata; what lives here is only what the degradation chain needs.
    """

    def __init__(self, adapters: Iterable[SourceAdapter] = ()) -> None:
        self._adapters: list[SourceAdapter] = list(adapters)

    def register(self, adapter: SourceAdapter) -> None:
        self._adapters.append(adapter)

    def all(self) -> tuple[SourceAdapter, ...]:
        return tuple(self._adapters)

    def candidates(self, request: FetchRequest) -> tuple[SourceAdapter, ...]:
        """Adapters that can serve part of the request, in deterministic priority order.

        A source is a candidate when it covers the box and publishes at least one of the
        requested variables — not only when it can serve everything, because a partial
        answer from an authoritative source plus a fallback for the remainder beats
        dropping the authoritative source entirely.
        """
        candidates = [
            adapter
            for adapter in self._adapters
            if adapter.covers(request)
            and any(adapter.descriptor.serves(v) for v in request.variables)
        ]
        return tuple(
            sorted(candidates, key=lambda a: (a.descriptor.authority_rank, a.descriptor.source_id))
        )


class ResilientIngestor:
    """Executes a fetch across the fallback chain, recording every attempt."""

    def __init__(self, registry: SourceRegistry, *, reject_stale: bool = True) -> None:
        self._registry = registry
        self._reject_stale = reject_stale

    async def fetch(self, request: FetchRequest, *, now: datetime) -> ResolvedFetch:
        """Try sources in priority order until every variable is served.

        ``now`` is injected rather than read from the clock so that staleness decisions
        are reproducible in tests and in replay.
        """
        outstanding = set(request.variables)
        records: list[ObservationRecord] = []
        attempts: list[SourceAttempt] = []
        used: list[str] = []
        archive_refs: list[ArchiveRef] = []

        for adapter in self._registry.candidates(request):
            if not outstanding:
                break

            descriptor = adapter.descriptor
            servable = frozenset(v for v in outstanding if descriptor.serves(v))
            if not servable:
                continue

            scoped = FetchRequest(
                bbox=request.bbox, time_window=request.time_window, variables=servable
            )
            started = time.perf_counter()
            try:
                result = await adapter.fetch(scoped)
            except SourceUnavailableError as exc:
                attempts.append(
                    SourceAttempt(
                        source_id=descriptor.source_id,
                        outcome=AttemptOutcome.UNAVAILABLE,
                        reason=exc.reason,
                        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                    )
                )
                continue

            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            archive_refs.extend(result.raw_refs)

            if result.is_empty:
                attempts.append(
                    SourceAttempt(
                        source_id=descriptor.source_id,
                        outcome=AttemptOutcome.EMPTY,
                        reason="source returned no records for this box and window",
                        elapsed_ms=elapsed_ms,
                        archive_refs=result.raw_refs,
                    )
                )
                continue

            fresh, stale = self._split_on_staleness(result.records, adapter, now)

            if self._reject_stale and not fresh:
                oldest = max(stale, key=lambda r: r.age(now))
                attempts.append(
                    SourceAttempt(
                        source_id=descriptor.source_id,
                        outcome=AttemptOutcome.STALE,
                        reason=(
                            f"all {len(stale)} record(s) exceed the {descriptor.cadence.deadline} "
                            f"cadence deadline; oldest is {oldest.age(now)} old"
                        ),
                        record_count=len(stale),
                        elapsed_ms=elapsed_ms,
                        archive_refs=result.raw_refs,
                    )
                )
                continue

            accepted = fresh if self._reject_stale else [*fresh, *stale]
            records.extend(accepted)
            used.append(descriptor.source_id)
            outstanding -= {r.variable for r in accepted}
            reason = (
                f"{len(stale)} stale record(s) dropped" if stale and self._reject_stale else None
            )
            attempts.append(
                SourceAttempt(
                    source_id=descriptor.source_id,
                    outcome=AttemptOutcome.SUCCESS,
                    reason=reason,
                    record_count=len(accepted),
                    elapsed_ms=elapsed_ms,
                    archive_refs=result.raw_refs,
                )
            )

        return ResolvedFetch(
            records=tuple(records),
            attempts=tuple(attempts),
            used_sources=tuple(used),
            missing_variables=frozenset(outstanding),
            archive_refs=tuple(archive_refs),
        )

    def _split_on_staleness(
        self, records: Sequence[ObservationRecord], adapter: SourceAdapter, now: datetime
    ) -> tuple[list[ObservationRecord], list[ObservationRecord]]:
        cadence = adapter.descriptor.cadence
        fresh: list[ObservationRecord] = []
        stale: list[ObservationRecord] = []
        for record in records:
            (stale if record.is_stale(cadence, now) else fresh).append(record)
        return fresh, stale
