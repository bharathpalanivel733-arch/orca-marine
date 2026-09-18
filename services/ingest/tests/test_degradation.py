"""Degradation policy and the staleness guarantee (PLAN.md Phase 1.11).

The central test in this file is ``TestStaleDataIsNeverSilentlyServed``. Everything else
supports it. If ORCA ever serves a decade-old SST as if it were today's, it is these
assertions that should have failed first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from orca_schemas import (
    AttemptOutcome,
    BoundingBox,
    Cadence,
    DataQuality,
    MarineVariable,
    ObservationRecord,
    SourceDescriptor,
    TimeWindow,
)

from orca_ingest import FetchRequest, FetchResult, SourceAdapter, SourceUnavailableError
from orca_ingest.degradation import ResilientIngestor, SourceRegistry

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
BOX = BoundingBox(min_lat=5, max_lat=25, min_lon=70, max_lon=95)
SST = MarineVariable.SEA_SURFACE_TEMPERATURE
WAVES = MarineVariable.SIGNIFICANT_WAVE_HEIGHT


class StubAdapter(SourceAdapter):
    """Adapter with scripted behaviour, for exercising the policy."""

    def __init__(
        self,
        source_id: str,
        *,
        rank: int,
        variables: set[MarineVariable],
        issued_age: timedelta = timedelta(0),
        cadence_period: timedelta = timedelta(hours=12),
        fail: str | None = None,
        empty: bool = False,
        coverage: BoundingBox = BOX,
    ) -> None:
        self._source_id = source_id
        self._rank = rank
        self._variables = variables
        self._issued_age = issued_age
        self._cadence_period = cadence_period
        self._fail = fail
        self._empty = empty
        self._coverage = coverage
        self.call_count = 0

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id=self._source_id,
            name=self._source_id,
            authority_rank=self._rank,
            variables=frozenset(self._variables),
            coverage=self._coverage,
            cadence=Cadence(period=self._cadence_period),
            license="test",
        )

    async def fetch(self, request: FetchRequest) -> FetchResult:
        self.call_count += 1
        if self._fail:
            raise SourceUnavailableError(self._source_id, self._fail)
        if self._empty:
            return FetchResult(source_id=self._source_id, retrieved_at=NOW)

        issued = NOW - self._issued_age
        records = tuple(
            ObservationRecord(
                variable=variable,
                value=1.5 if variable is WAVES else 29.0,
                unit="m" if variable is WAVES else "degC",
                lat=13.1,
                lon=80.3,
                valid_time=NOW,
                issued_time=issued,
                source=self._source_id,
                quality=DataQuality.OBSERVED,
                license="test",
            )
            for variable in sorted(request.variables & self._variables)
        )
        return FetchResult(source_id=self._source_id, records=records, retrieved_at=NOW)


def request_for(*variables: MarineVariable) -> FetchRequest:
    return FetchRequest(
        bbox=BOX,
        time_window=TimeWindow(start=NOW - timedelta(hours=1), end=NOW + timedelta(hours=1)),
        variables=frozenset(variables),
    )


class TestStaleDataIsNeverSilentlyServed:
    """The guarantee: stale records never reach a caller as if they were current."""

    async def test_stale_source_is_rejected_and_the_chain_falls_through(self) -> None:
        # Mirrors reality: INCOIS ERDDAP's SST archive ends in 2014, so its records are
        # stale by years, while a fallback has current data.
        archive = StubAdapter(
            "incois_erddap", rank=1, variables={SST}, issued_age=timedelta(days=4000)
        )
        live = StubAdapter("cmems", rank=2, variables={SST}, issued_age=timedelta(hours=1))
        ingestor = ResilientIngestor(SourceRegistry([archive, live]))

        resolved = await ingestor.fetch(request_for(SST), now=NOW)

        assert [r.source for r in resolved.records] == ["cmems"]
        assert not any(r.source == "incois_erddap" for r in resolved.records)

        stale_attempts = resolved.attempts_for(AttemptOutcome.STALE)
        assert len(stale_attempts) == 1
        assert stale_attempts[0].source_id == "incois_erddap"
        assert "cadence deadline" in (stale_attempts[0].reason or "")

    async def test_all_sources_stale_yields_nothing_rather_than_old_data(self) -> None:
        """With every source stale the correct answer is silence, not last week's data."""
        first = StubAdapter("incois_erddap", rank=1, variables={SST}, issued_age=timedelta(days=30))
        second = StubAdapter("cmems", rank=2, variables={SST}, issued_age=timedelta(days=5))
        ingestor = ResilientIngestor(SourceRegistry([first, second]))

        resolved = await ingestor.fetch(request_for(SST), now=NOW)

        assert resolved.records == ()
        assert resolved.is_empty
        assert SST in resolved.missing_variables
        assert len(resolved.attempts_for(AttemptOutcome.STALE)) == 2
        # The caller can see exactly why it got nothing.
        assert "incois_erddap=stale" in resolved.explain()

    async def test_staleness_is_evaluated_against_the_sources_own_cadence(self) -> None:
        """Six hours is fresh for a 3x-weekly advisory and stale for an hourly buoy."""
        slow = StubAdapter(
            "pfz",
            rank=1,
            variables={SST},
            issued_age=timedelta(hours=6),
            cadence_period=timedelta(hours=56),
        )
        fast = StubAdapter(
            "buoy",
            rank=1,
            variables={SST},
            issued_age=timedelta(hours=6),
            cadence_period=timedelta(hours=1),
        )

        slow_result = await ResilientIngestor(SourceRegistry([slow])).fetch(
            request_for(SST), now=NOW
        )
        fast_result = await ResilientIngestor(SourceRegistry([fast])).fetch(
            request_for(SST), now=NOW
        )

        assert not slow_result.is_empty
        assert fast_result.is_empty

    async def test_stale_data_is_reachable_only_by_explicit_opt_in(self) -> None:
        """The offline path may show old data — but only when asked, and it is flagged."""
        stale = StubAdapter("incois_erddap", rank=1, variables={SST}, issued_age=timedelta(days=30))
        permissive = ResilientIngestor(SourceRegistry([stale]), reject_stale=False)

        resolved = await permissive.fetch(request_for(SST), now=NOW)

        assert not resolved.is_empty
        record = resolved.records[0]
        # Even opted in, the age is computable and the record is visibly old.
        assert record.age(NOW) == timedelta(days=30)
        assert record.is_stale(stale.descriptor.cadence, NOW)


class TestFallbackOrdering:
    async def test_authoritative_source_wins_when_fresh(self) -> None:
        authoritative = StubAdapter("incois", rank=1, variables={WAVES})
        fallback = StubAdapter("open_meteo_marine", rank=4, variables={WAVES})
        ingestor = ResilientIngestor(SourceRegistry([fallback, authoritative]))

        resolved = await ingestor.fetch(request_for(WAVES), now=NOW)

        assert resolved.used_sources == ("incois",)
        assert fallback.call_count == 0, "a satisfied request must not spend a fallback call"

    async def test_unavailable_source_falls_through_with_a_recorded_reason(self) -> None:
        down = StubAdapter("incois", rank=1, variables={WAVES}, fail="connection refused")
        up = StubAdapter("open_meteo_marine", rank=4, variables={WAVES})
        ingestor = ResilientIngestor(SourceRegistry([down, up]))

        resolved = await ingestor.fetch(request_for(WAVES), now=NOW)

        assert resolved.used_sources == ("open_meteo_marine",)
        unavailable = resolved.attempts_for(AttemptOutcome.UNAVAILABLE)
        assert unavailable[0].reason == "connection refused"
        assert resolved.degraded

    async def test_empty_is_distinct_from_unavailable(self) -> None:
        """'No data here' and 'source is down' must not collapse into one outcome."""
        empty = StubAdapter("incois", rank=1, variables={WAVES}, empty=True)
        ingestor = ResilientIngestor(SourceRegistry([empty]))

        resolved = await ingestor.fetch(request_for(WAVES), now=NOW)

        assert resolved.attempts_for(AttemptOutcome.EMPTY)
        assert not resolved.attempts_for(AttemptOutcome.UNAVAILABLE)

    async def test_variables_are_served_from_several_sources(self) -> None:
        """A partial answer from an authority plus a fallback beats dropping either."""
        sst_only = StubAdapter("incois", rank=1, variables={SST})
        wave_only = StubAdapter("open_meteo_marine", rank=4, variables={WAVES})
        ingestor = ResilientIngestor(SourceRegistry([sst_only, wave_only]))

        resolved = await ingestor.fetch(request_for(SST, WAVES), now=NOW)

        assert {r.variable for r in resolved.records} == {SST, WAVES}
        assert set(resolved.used_sources) == {"incois", "open_meteo_marine"}
        assert not resolved.missing_variables

    async def test_unserved_variable_is_reported_not_invented(self) -> None:
        ingestor = ResilientIngestor(
            SourceRegistry([StubAdapter("incois", rank=1, variables={SST})])
        )

        resolved = await ingestor.fetch(request_for(SST, WAVES), now=NOW)

        assert resolved.missing_variables == frozenset({WAVES})
        assert all(r.variable is SST for r in resolved.records)

    async def test_out_of_coverage_source_is_not_attempted(self) -> None:
        atlantic = BoundingBox(min_lat=0, max_lat=10, min_lon=-40, max_lon=-20)
        far_away = StubAdapter("atlantic", rank=1, variables={WAVES}, coverage=atlantic)
        local = StubAdapter("open_meteo_marine", rank=4, variables={WAVES})
        ingestor = ResilientIngestor(SourceRegistry([far_away, local]))

        resolved = await ingestor.fetch(request_for(WAVES), now=NOW)

        assert far_away.call_count == 0
        assert resolved.used_sources == ("open_meteo_marine",)


class TestProvenance:
    async def test_every_attempt_is_recorded_not_just_the_winner(self) -> None:
        """A decision must be able to show what was tried and rejected, and why."""
        stale = StubAdapter(
            "incois_erddap", rank=1, variables={WAVES}, issued_age=timedelta(days=9)
        )
        down = StubAdapter("cmems", rank=2, variables={WAVES}, fail="missing credentials")
        live = StubAdapter("open_meteo_marine", rank=4, variables={WAVES})
        ingestor = ResilientIngestor(SourceRegistry([stale, down, live]))

        resolved = await ingestor.fetch(request_for(WAVES), now=NOW)

        assert [a.source_id for a in resolved.attempts] == [
            "incois_erddap",
            "cmems",
            "open_meteo_marine",
        ]
        assert [a.outcome for a in resolved.attempts] == [
            AttemptOutcome.STALE,
            AttemptOutcome.UNAVAILABLE,
            AttemptOutcome.SUCCESS,
        ]
        assert "cmems=unavailable(missing credentials)" in resolved.explain()

    async def test_failed_attempts_must_carry_a_reason(self) -> None:
        from orca_schemas import SourceAttempt
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="must carry a reason"):
            SourceAttempt(source_id="x", outcome=AttemptOutcome.STALE)
