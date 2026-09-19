"""Bitemporal ``as_of`` querying of structured evidence (PLAN.md Phase 3.2).

Runs against the real database (``-m stack``).

The property under test is the one Phase 6.4's replay depends on: **a query anchored at a
past instant must see the forecast that was available then, not the revision issued
since.** Without it every replay would silently use better information than the original
decision had, and would therefore always look correct — which would make the whole trust
layer worthless.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from orca_schemas import (
    BoundingBox,
    DataQuality,
    MarineVariable,
    MeasurementKind,
    ObservationRecord,
    TimeWindow,
)

from orca_evidence import StructuredEvidenceStore

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://orca:orca@localhost:5432/orca")
MIGRATIONS = Path(__file__).resolve().parents[3] / "infra" / "db" / "migrations"

WAVES = MarineVariable.SIGNIFICANT_WAVE_HEIGHT
PALK_BAY = BoundingBox(min_lat=9.0, max_lat=10.0, min_lon=79.0, max_lon=80.0)

TOMORROW_0600 = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
FIRST_ISSUE = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)
SECOND_ISSUE = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

SOURCE = "as_of_test"


def record(*, value: float, issued_time: datetime, valid_time: datetime = TOMORROW_0600):
    return ObservationRecord(
        variable=WAVES,
        value=value,
        unit="m",
        lat=9.6,
        lon=79.4,
        valid_time=valid_time,
        issued_time=issued_time,
        source=SOURCE,
        dataset_id="test",
        quality=DataQuality.OBSERVED,
        kind=MeasurementKind.FORECAST,
        license="test",
    )


@pytest.fixture
def store():
    psycopg = pytest.importorskip("psycopg")
    try:
        connection = psycopg.connect(DATABASE_URL, connect_timeout=5)
    except psycopg.OperationalError as exc:
        pytest.skip(f"dev database not reachable ({exc}); run `pnpm db:up`")

    from orca_ingest.storage import TimescaleObservationStore

    writer = TimescaleObservationStore(connection)
    writer.ensure_schema()
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM evidence.observations WHERE source = %s", (SOURCE,))
    connection.commit()

    # Two issues of the same forecast: the sea state was revised upward at midday.
    writer.write([record(value=1.2, issued_time=FIRST_ISSUE)])
    writer.write([record(value=3.4, issued_time=SECOND_ISSUE)])

    yield StructuredEvidenceStore(connection, authority_ranks={SOURCE: 2})

    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM evidence.observations WHERE source = %s", (SOURCE,))
    connection.commit()
    connection.close()


def window_around(moment: datetime) -> TimeWindow:
    return TimeWindow(start=moment - timedelta(hours=1), end=moment + timedelta(hours=1))


@pytest.mark.stack
class TestAsOfSemantics:
    def test_as_of_before_the_revision_sees_the_original_forecast(self, store) -> None:
        """Replaying the morning's decision must see the morning's forecast."""
        results = store.query(
            variable=WAVES,
            bbox=PALK_BAY,
            window=window_around(TOMORROW_0600),
            as_of=SECOND_ISSUE - timedelta(minutes=1),
        )

        assert len(results) == 1
        assert results[0].value == pytest.approx(1.2)

    def test_as_of_after_the_revision_sees_the_revised_forecast(self, store) -> None:
        results = store.query(
            variable=WAVES,
            bbox=PALK_BAY,
            window=window_around(TOMORROW_0600),
            as_of=SECOND_ISSUE + timedelta(hours=1),
        )

        assert len(results) == 1
        assert results[0].value == pytest.approx(3.4)

    def test_only_one_row_per_natural_key_is_returned(self, store) -> None:
        """Two issues of one forecast must not both be served as evidence."""
        results = store.query(
            variable=WAVES,
            bbox=PALK_BAY,
            window=window_around(TOMORROW_0600),
            as_of=datetime(2026, 9, 25, tzinfo=UTC),
        )

        assert len(results) == 1

    def test_as_of_before_any_issue_returns_nothing(self, store) -> None:
        results = store.query(
            variable=WAVES,
            bbox=PALK_BAY,
            window=window_around(TOMORROW_0600),
            as_of=FIRST_ISSUE - timedelta(days=1),
        )

        assert results == ()

    def test_bbox_excludes_positions_outside_the_query(self, store) -> None:
        elsewhere = BoundingBox(min_lat=20.0, max_lat=21.0, min_lon=70.0, max_lon=71.0)

        results = store.query(
            variable=WAVES,
            bbox=elsewhere,
            window=window_around(TOMORROW_0600),
            as_of=SECOND_ISSUE + timedelta(hours=1),
        )

        assert results == ()

    def test_valid_time_window_is_honoured(self, store) -> None:
        results = store.query(
            variable=WAVES,
            bbox=PALK_BAY,
            window=window_around(TOMORROW_0600 + timedelta(days=3)),
            as_of=SECOND_ISSUE + timedelta(hours=1),
        )

        assert results == ()

    def test_naive_as_of_is_rejected(self, store) -> None:
        with pytest.raises(ValueError, match="as_of must be timezone-aware"):
            store.query(
                variable=WAVES,
                bbox=PALK_BAY,
                window=window_around(TOMORROW_0600),
                as_of=datetime(2026, 9, 19, 12, 0),  # noqa: DTZ001
            )

    def test_evidence_carries_provenance(self, store) -> None:
        results = store.query(
            variable=WAVES,
            bbox=PALK_BAY,
            window=window_around(TOMORROW_0600),
            as_of=SECOND_ISSUE + timedelta(hours=1),
        )

        provenance = results[0].provenance
        assert provenance.source == SOURCE
        assert provenance.issued_time == SECOND_ISSUE
        assert provenance.authority_rank == 2
        assert provenance.license == "test"
        assert SOURCE in provenance.provenance_id
        assert provenance.age_hours(SECOND_ISSUE + timedelta(hours=1)) == pytest.approx(1.0)

    def test_latest_issue_time_reports_the_freshness_the_gate_needs(self, store) -> None:
        issues = store.latest_issue_times(variable=WAVES, as_of=SECOND_ISSUE + timedelta(hours=1))

        by_source = dict(issues)
        assert by_source[SOURCE] == SECOND_ISSUE

    def test_latest_issue_time_respects_as_of(self, store) -> None:
        issues = store.latest_issue_times(variable=WAVES, as_of=SECOND_ISSUE - timedelta(minutes=1))

        assert dict(issues)[SOURCE] == FIRST_ISSUE
