"""Integration tests against the running dev stack and live endpoints.

Two opt-in groups, both skipped by default so the normal suite stays hermetic:

* ``-m stack`` — TimescaleDB and MinIO from ``infra/docker-compose.yml``. Skipped with a
  clear reason when the stack is not up, never silently passed.
* ``-m live`` — real upstream endpoints. Only keyless sources are exercised; a
  credentialed source is never "tested" by pretending.

Run: ``pytest -m stack`` / ``pytest -m live`` / ``pytest -m "stack or live"``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from orca_schemas import (
    BoundingBox,
    DataQuality,
    MarineVariable,
    MeasurementKind,
    ObservationRecord,
    TimeWindow,
)

from orca_ingest import FetchRequest, S3Archive
from orca_ingest.adapters import IncoisErddapAdapter, OpenMeteoMarineAdapter
from orca_ingest.storage import TimescaleObservationStore, build_s3_client, ensure_bucket

CHENNAI = BoundingBox(min_lat=12.9, max_lat=13.3, min_lon=80.1, max_lon=80.5)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://orca:orca@localhost:5432/orca")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000")
S3_KEY = os.environ.get("S3_ACCESS_KEY_ID", "orca-minio")
S3_SECRET = os.environ.get("S3_SECRET_ACCESS_KEY", "orca-minio-secret")
S3_BUCKET = os.environ.get("S3_BUCKET", "orca-cache")


@pytest.fixture
def connection():
    """A psycopg connection to the dev database, or skip."""
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(DATABASE_URL, connect_timeout=5)
    except psycopg.OperationalError as exc:
        pytest.skip(f"dev database not reachable ({exc}); run `pnpm db:up`")
    yield conn
    conn.close()


@pytest.fixture
def s3_client():
    """A boto3 client against MinIO, or skip."""
    pytest.importorskip("boto3")
    from botocore.exceptions import BotoCoreError, ClientError

    client = build_s3_client(endpoint_url=S3_ENDPOINT, access_key=S3_KEY, secret_key=S3_SECRET)
    try:
        ensure_bucket(client, S3_BUCKET)
    except (BotoCoreError, ClientError) as exc:
        pytest.skip(f"MinIO not reachable ({exc}); run `pnpm db:up`")
    return client


def make_record(**overrides) -> ObservationRecord:
    defaults = {
        "variable": MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
        "value": 1.42,
        "unit": "m",
        "lat": 13.125,
        "lon": 80.25,
        "valid_time": datetime(2026, 9, 18, 6, tzinfo=UTC),
        "issued_time": datetime(2026, 9, 18, 5, tzinfo=UTC),
        "source": "integration_test",
        "quality": DataQuality.OBSERVED,
        "kind": MeasurementKind.FORECAST,
        "license": "test",
    }
    return ObservationRecord(**(defaults | overrides))


@pytest.mark.stack
class TestTimescalePersistence:
    def test_schema_applies_and_is_idempotent(self, connection) -> None:
        store = TimescaleObservationStore(connection)
        store.ensure_schema()
        store.ensure_schema()  # must not fail on a second run

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_schema = 'evidence' AND hypertable_name = 'observations'"
            )
            assert cursor.fetchone()[0] == 1

    def test_records_round_trip(self, connection) -> None:
        store = TimescaleObservationStore(connection)
        store.ensure_schema()
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM evidence.observations WHERE source = 'integration_test'")
        connection.commit()

        written = store.write([make_record(), make_record(value=1.55, lat=13.375)])
        assert written == 2

        rows = store.read(
            variable=MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
            start=datetime(2026, 9, 18, tzinfo=UTC),
            end=datetime(2026, 9, 19, tzinfo=UTC),
            min_lat=12.9,
            max_lat=13.5,
            min_lon=80.0,
            max_lon=80.5,
        )
        ours = [r for r in rows if r.source == "integration_test"]
        assert len(ours) == 2
        assert {r.value for r in ours} == {1.42, 1.55}
        assert ours[0].unit == "m"
        assert ours[0].issued_time == datetime(2026, 9, 18, 5, tzinfo=UTC)

    def test_reingesting_the_same_record_updates_rather_than_duplicates(self, connection) -> None:
        """Replay and the cadence cache both re-fetch the same window constantly."""
        store = TimescaleObservationStore(connection)
        store.ensure_schema()
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM evidence.observations WHERE source = 'integration_test'")
        connection.commit()

        store.write([make_record(value=1.42)])
        store.write([make_record(value=1.99)])  # same natural key, revised value

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*), max(value) FROM evidence.observations "
                "WHERE source = 'integration_test'"
            )
            count, value = cursor.fetchone()
        assert count == 1
        assert float(value) == 1.99

    def test_database_rejects_a_value_that_contradicts_its_quality(self, connection) -> None:
        """The invariant is enforced in the schema too, not only in Pydantic."""
        import psycopg

        store = TimescaleObservationStore(connection)
        store.ensure_schema()

        with pytest.raises(psycopg.errors.CheckViolation), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO evidence.observations (variable, value, unit, lat, lon, "
                "valid_time, issued_time, source, quality, kind, license) VALUES "
                "('significant_wave_height', NULL, 'm', 13.1, 80.3, now(), now(), "
                "'integration_test', 'observed', 'forecast', 'test')"
            )
        connection.rollback()


@pytest.mark.stack
class TestObjectStorage:
    def test_raw_payload_round_trips_through_minio(self, s3_client) -> None:
        archive = S3Archive(s3_client, S3_BUCKET)
        payload = b'{"hourly": {"wave_height": [0.48, 0.5]}}'

        ref = archive.store(
            payload,
            source_id="integration_test",
            content_type="application/json",
            retrieved_at=datetime.now(UTC),
            dataset_id="marine",
        )

        assert ref.uri.startswith(f"s3://{S3_BUCKET}/raw/integration_test/")
        assert archive.read(ref) == payload

    def test_zarr_field_is_written(self, tmp_path) -> None:
        """Gridded persistence, via the optional 'grids' extra."""
        xr = pytest.importorskip("xarray")
        np = pytest.importorskip("numpy")
        pytest.importorskip("zarr")

        from orca_ingest.storage import GridStore

        dataset = xr.Dataset(
            {"significant_wave_height": (("time", "lat", "lon"), np.zeros((2, 3, 3)) + 1.4)},
            coords={
                "time": [datetime(2026, 9, 18, 0), datetime(2026, 9, 18, 3)],
                "lat": [12.9, 13.1, 13.3],
                "lon": [80.1, 80.3, 80.5],
            },
        )
        store = GridStore(
            endpoint_url=S3_ENDPOINT, access_key=S3_KEY, secret_key=S3_SECRET, bucket=S3_BUCKET
        )

        path = store.write_zarr_local(dataset, str(tmp_path / "waves.zarr"))

        reopened = xr.open_zarr(path)
        assert reopened["significant_wave_height"].shape == (2, 3, 3)

    def test_cog_is_honestly_unimplemented(self) -> None:
        from orca_ingest.storage import GridStore

        store = GridStore(
            endpoint_url=S3_ENDPOINT, access_key=S3_KEY, secret_key=S3_SECRET, bucket=S3_BUCKET
        )
        with pytest.raises(NotImplementedError, match="requires rasterio"):
            store.write_cog(object(), "x.tif")


@pytest.mark.live
class TestLiveKeylessSources:
    """Real network calls to keyless endpoints."""

    async def test_open_meteo_returns_current_wave_forecasts(self) -> None:
        adapter = OpenMeteoMarineAdapter()
        now = datetime.now(UTC)
        request = FetchRequest(
            bbox=CHENNAI,
            time_window=TimeWindow(start=now, end=now + timedelta(hours=12)),
            variables=frozenset(
                {MarineVariable.SIGNIFICANT_WAVE_HEIGHT, MarineVariable.PEAK_WAVE_PERIOD}
            ),
        )

        result = await adapter.fetch(request)

        assert not result.is_empty
        waves = result.records_for(MarineVariable.SIGNIFICANT_WAVE_HEIGHT)
        assert waves
        assert all(r.unit == "m" for r in waves)
        assert all(r.kind is MeasurementKind.FORECAST for r in waves)
        # Sanity: a plausible sea state off Chennai, not a unit-scale blunder.
        assert all(0 <= r.value <= 20 for r in waves if r.value is not None)

    async def test_erddap_metadata_discovery_reports_real_structure(self) -> None:
        """Dimensions and coverage come from the server, never from a constant."""
        adapter = IncoisErddapAdapter(dataset_id="incois_tmi_3day_datasets")

        meta = await adapter.discover()

        assert "time" in meta.dimensions
        assert "SST" in meta.variables
        assert meta.time_coverage_end is not None
        # Documents the archive finding: this dataset stopped long ago.
        assert meta.time_coverage_end.year <= 2015
        assert meta.uses_0_360_longitude

    async def test_erddap_refuses_a_present_day_window_for_an_archive(self) -> None:
        adapter = IncoisErddapAdapter(dataset_id="incois_tmi_3day_datasets")
        now = datetime.now(UTC)
        request = FetchRequest(
            bbox=CHENNAI,
            time_window=TimeWindow(start=now, end=now + timedelta(hours=6)),
            variables=frozenset({MarineVariable.SEA_SURFACE_TEMPERATURE}),
        )

        result = await adapter.fetch(request)

        assert result.is_empty
        assert any("historical archive" in w for w in result.warnings)
