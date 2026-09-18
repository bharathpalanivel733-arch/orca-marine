"""Adapter interface tests (PLAN.md Phase 1.1).

Exercised through a fake in-memory adapter: the point is to prove the *contract* is
usable and behaves deterministically, not to contact any real source. No network call
is made from this file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from orca_schemas import (
    BoundingBox,
    Cadence,
    DataQuality,
    MarineVariable,
    ObservationRecord,
    SourceDescriptor,
    TimeWindow,
)

from orca_ingest import FetchRequest, FetchResult, SourceAdapter, SourceUnavailableError

T0 = datetime(2026, 9, 18, 6, 0, tzinfo=UTC)
BAY_OF_BENGAL = BoundingBox(min_lat=5, max_lat=25, min_lon=70, max_lon=95)
ARABIAN_SEA = BoundingBox(min_lat=5, max_lat=25, min_lon=60, max_lon=78)
ATLANTIC = BoundingBox(min_lat=0, max_lat=10, min_lon=-40, max_lon=-20)


class FakeWaveAdapter(SourceAdapter):
    """A source serving wave height over the Bay of Bengal, from memory."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="fake_waves",
            name="Fake Wave Source",
            authority_rank=3,
            variables=frozenset(
                {MarineVariable.SIGNIFICANT_WAVE_HEIGHT, MarineVariable.PEAK_WAVE_PERIOD}
            ),
            coverage=BAY_OF_BENGAL,
            cadence=Cadence(period=timedelta(hours=12), grace=timedelta(hours=1)),
            license="CC-BY-4.0",
            attribution="Fake Wave Source",
        )

    async def fetch(self, request: FetchRequest) -> FetchResult:
        if self._fail:
            raise SourceUnavailableError(self.descriptor.source_id, "connection refused")

        unsupported = self.unsupported_variables(request)
        records = tuple(
            ObservationRecord(
                variable=MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
                value=1.4,
                unit="m",
                lat=13.1,
                lon=80.3,
                valid_time=request.time_window.start,
                issued_time=T0,
                source=self.descriptor.source_id,
                license=self.descriptor.license,
            )
            for _ in range(1)
            if MarineVariable.SIGNIFICANT_WAVE_HEIGHT in request.variables
        )
        return FetchResult(
            source_id=self.descriptor.source_id,
            records=records,
            retrieved_at=T0,
            missing_variables=unsupported,
            warnings=(f"not served: {sorted(unsupported)}",) if unsupported else (),
        )


def make_request(
    *, bbox: BoundingBox = BAY_OF_BENGAL, variables: set[MarineVariable] | None = None
) -> FetchRequest:
    return FetchRequest(
        bbox=bbox,
        time_window=TimeWindow(start=T0, end=T0 + timedelta(hours=24)),
        variables=frozenset(variables or {MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
    )


class TestCapabilityChecks:
    """`supports` must answer 'could this source serve this' without a network call."""

    def test_supported_request(self) -> None:
        assert FakeWaveAdapter().supports(make_request())

    def test_unserved_variable_is_reported(self) -> None:
        adapter = FakeWaveAdapter()
        request = make_request(variables={MarineVariable.CHLOROPHYLL})
        assert adapter.unsupported_variables(request) == frozenset({MarineVariable.CHLOROPHYLL})
        assert not adapter.supports(request)

    def test_overlapping_coverage_counts_as_covered(self) -> None:
        assert FakeWaveAdapter().covers(make_request(bbox=ARABIAN_SEA))

    def test_disjoint_coverage_is_not_supported(self) -> None:
        adapter = FakeWaveAdapter()
        assert not adapter.covers(make_request(bbox=ATLANTIC))
        assert not adapter.supports(make_request(bbox=ATLANTIC))


class TestFetch:
    async def test_returns_canonical_records(self) -> None:
        result = await FakeWaveAdapter().fetch(make_request())
        assert result.source_id == "fake_waves"
        assert len(result.records) == 1
        record = result.records[0]
        assert record.unit == "m"
        assert record.source == "fake_waves"
        assert record.quality is DataQuality.OBSERVED

    async def test_partial_success_is_not_an_exception(self) -> None:
        """A variable the source cannot serve is reported, not raised."""
        request = make_request(
            variables={MarineVariable.SIGNIFICANT_WAVE_HEIGHT, MarineVariable.CHLOROPHYLL}
        )
        result = await FakeWaveAdapter().fetch(request)
        assert result.missing_variables == frozenset({MarineVariable.CHLOROPHYLL})
        assert result.records_for(MarineVariable.SIGNIFICANT_WAVE_HEIGHT)
        assert result.warnings

    async def test_source_failure_raises_rather_than_returning_empty(self) -> None:
        """'Source is down' must be distinguishable from 'source has no data here'."""
        with pytest.raises(SourceUnavailableError) as excinfo:
            await FakeWaveAdapter(fail=True).fetch(make_request())
        assert excinfo.value.source_id == "fake_waves"
        assert "connection refused" in str(excinfo.value)

    async def test_empty_result_is_expressible(self) -> None:
        result = FetchResult(source_id="fake_waves", retrieved_at=T0)
        assert result.is_empty
        assert result.records_for(MarineVariable.SIGNIFICANT_WAVE_HEIGHT) == ()


class TestStalenessOfResults:
    async def test_stale_records_are_identified_against_the_source_cadence(self) -> None:
        adapter = FakeWaveAdapter()
        result = await adapter.fetch(make_request())
        cadence = adapter.descriptor.cadence

        fresh_now = T0 + timedelta(hours=12)
        assert result.stale_records(cadence, fresh_now) == ()

        stale_now = T0 + timedelta(hours=14)
        assert len(result.stale_records(cadence, stale_now)) == 1


def test_fetch_request_requires_at_least_one_variable() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FetchRequest(
            bbox=BAY_OF_BENGAL,
            time_window=TimeWindow(start=T0, end=T0 + timedelta(hours=1)),
            variables=frozenset(),
        )
