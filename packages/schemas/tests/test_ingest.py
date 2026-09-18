"""Contract tests for the ingestion record (PLAN.md Phase 1.1).

These lock the three invariants the rest of the system relies on: canonical units,
missing data being representable, and deterministic staleness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from orca_schemas import (
    CANONICAL_UNITS,
    BoundingBox,
    Cadence,
    DataQuality,
    MarineVariable,
    MeasurementKind,
    ObservationRecord,
    SourceDescriptor,
    TimeWindow,
)
from pydantic import ValidationError

T0 = datetime(2026, 9, 18, 6, 0, tzinfo=UTC)
BAY_OF_BENGAL = BoundingBox(min_lat=5, max_lat=25, min_lon=70, max_lon=95)


def make_record(**overrides: object) -> ObservationRecord:
    defaults: dict[str, object] = {
        "variable": MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
        "value": 1.4,
        "unit": "m",
        "lat": 13.1,
        "lon": 80.3,
        "valid_time": T0,
        "issued_time": T0,
        "source": "cmems",
        "license": "CC-BY-4.0",
    }
    return ObservationRecord(**(defaults | overrides))  # type: ignore[arg-type]


class TestUnits:
    def test_canonical_unit_is_accepted(self) -> None:
        assert make_record().unit == "m"

    def test_every_variable_has_a_canonical_unit(self) -> None:
        assert set(CANONICAL_UNITS) == set(MarineVariable)

    def test_wrong_unit_is_rejected_with_a_useful_message(self) -> None:
        """A wave height in centimetres must never reach a safety threshold."""
        with pytest.raises(ValidationError, match="must be expressed in 'm'"):
            make_record(unit="cm", value=140.0)

    def test_unit_mismatch_across_variables_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be expressed in 'degC'"):
            make_record(variable=MarineVariable.SEA_SURFACE_TEMPERATURE, unit="m")


class TestMissingData:
    def test_missing_quality_allows_no_value(self) -> None:
        record = make_record(value=None, quality=DataQuality.MISSING)
        assert record.value is None

    def test_missing_quality_rejects_a_value(self) -> None:
        with pytest.raises(ValidationError, match="must not carry a value"):
            make_record(value=1.2, quality=DataQuality.MISSING)

    def test_none_value_requires_missing_quality(self) -> None:
        with pytest.raises(ValidationError, match="may be None only when quality is"):
            make_record(value=None)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_values_are_rejected(self, bad: float) -> None:
        """NaN is how a NetCDF fill value leaks in; it must not become a number."""
        with pytest.raises(ValidationError, match="must be finite"):
            make_record(value=bad)


class TestGeometryAndTime:
    @pytest.mark.parametrize(
        ("lat", "lon"), [(91, 80), (-91, 80), (13, 181), (13, -181)]
    )
    def test_out_of_range_coordinates_are_rejected(
        self, lat: float, lon: float
    ) -> None:
        with pytest.raises(ValidationError):
            make_record(lat=lat, lon=lon)

    def test_naive_datetimes_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            make_record(valid_time=datetime(2026, 9, 18, 6, 0))  # noqa: DTZ001

    def test_negative_depth_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_record(depth_m=-5)

    def test_bbox_ordering_is_enforced(self) -> None:
        with pytest.raises(ValidationError, match="min_lat must not exceed max_lat"):
            BoundingBox(min_lat=25, max_lat=5, min_lon=70, max_lon=95)

    def test_bbox_contains_and_intersects(self) -> None:
        assert BAY_OF_BENGAL.contains(13.1, 80.3)
        assert not BAY_OF_BENGAL.contains(13.1, 60.0)
        arabian_sea = BoundingBox(min_lat=5, max_lat=25, min_lon=60, max_lon=78)
        assert BAY_OF_BENGAL.intersects(arabian_sea)
        atlantic = BoundingBox(min_lat=0, max_lat=10, min_lon=-40, max_lon=-20)
        assert not BAY_OF_BENGAL.intersects(atlantic)

    def test_time_window_is_half_open(self) -> None:
        window = TimeWindow(start=T0, end=T0 + timedelta(hours=6))
        assert window.contains(T0)
        assert not window.contains(T0 + timedelta(hours=6))

    def test_time_window_rejects_zero_length(self) -> None:
        with pytest.raises(ValidationError, match="strictly before"):
            TimeWindow(start=T0, end=T0)


class TestStaleness:
    """Staleness is arithmetic, not judgement — same inputs, same verdict, always."""

    def test_within_cadence_is_fresh(self) -> None:
        cadence = Cadence(period=timedelta(hours=12))
        assert not cadence.is_stale(T0, T0 + timedelta(hours=11, minutes=59))

    def test_beyond_cadence_is_stale(self) -> None:
        cadence = Cadence(period=timedelta(hours=12))
        assert cadence.is_stale(T0, T0 + timedelta(hours=12, seconds=1))

    def test_grace_extends_the_deadline(self) -> None:
        cadence = Cadence(period=timedelta(hours=12), grace=timedelta(hours=2))
        assert cadence.deadline == timedelta(hours=14)
        assert not cadence.is_stale(T0, T0 + timedelta(hours=13))
        assert cadence.is_stale(T0, T0 + timedelta(hours=15))

    def test_pfz_three_times_weekly_cadence(self) -> None:
        """INCOIS PFZ is issued 3x/week (DEPLOYMENT.md §2), so ~56 h between issues."""
        cadence = Cadence(period=timedelta(hours=56), grace=timedelta(hours=8))
        assert not cadence.is_stale(T0, T0 + timedelta(days=2))
        assert cadence.is_stale(T0, T0 + timedelta(days=3))

    def test_record_age_and_staleness_agree(self) -> None:
        record = make_record(issued_time=T0)
        now = T0 + timedelta(hours=8)
        assert record.age(now) == timedelta(hours=8)
        assert record.is_stale(Cadence(period=timedelta(hours=6)), now)
        assert not record.is_stale(Cadence(period=timedelta(hours=12)), now)

    def test_cadence_rejects_non_positive_period(self) -> None:
        with pytest.raises(ValidationError, match="period must be positive"):
            Cadence(period=timedelta(0))


class TestSourceDescriptor:
    def test_serves_reports_declared_variables(self) -> None:
        descriptor = SourceDescriptor(
            source_id="cmems",
            name="Copernicus Marine",
            authority_rank=2,
            variables=frozenset({MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
            coverage=BAY_OF_BENGAL,
            cadence=Cadence(period=timedelta(hours=12)),
            license="CC-BY-4.0",
        )
        assert descriptor.serves(MarineVariable.SIGNIFICANT_WAVE_HEIGHT)
        assert not descriptor.serves(MarineVariable.CHLOROPHYLL)


def test_record_round_trips_through_json() -> None:
    record = make_record(
        kind=MeasurementKind.FORECAST, dataset_id="cmems_mod_glo_wav_anfc"
    )
    assert ObservationRecord.model_validate_json(record.model_dump_json()) == record
