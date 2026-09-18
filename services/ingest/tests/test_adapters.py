"""Adapter tests against recorded upstream payloads (PLAN.md Phase 1.2-1.9).

Payload shapes here were captured from the real services on 2026-09-18, so these tests
exercise the parsers against what the upstreams actually return rather than an invented
shape. No network call is made: an ``httpx.MockTransport`` serves the recorded bytes.

Live tests live in ``test_live_sources.py`` and are opt-in.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from orca_schemas import BoundingBox, DataQuality, MarineVariable, MeasurementKind, TimeWindow

from orca_ingest import FetchRequest, HttpFetcher, SourceUnavailableError
from orca_ingest.adapters import (
    CmemsAdapter,
    ImdAdapter,
    IncoisErddapAdapter,
    IncoisOsfAdapter,
    IncoisPfzAdapter,
    MosdacAdapter,
    NiotOmniBuoyAdapter,
    OpenMeteoMarineAdapter,
)
from orca_ingest.adapters.incois_erddap import DatasetMetadata

BOX = BoundingBox(min_lat=12.9, max_lat=13.3, min_lon=80.1, max_lon=80.5)
WINDOW = TimeWindow(
    start=datetime(2026, 9, 18, 0, tzinfo=UTC), end=datetime(2026, 9, 18, 6, tzinfo=UTC)
)

# Captured from marine-api.open-meteo.com on 2026-09-18.
OPEN_METEO_PAYLOAD = {
    "latitude": 13.125,
    "longitude": 80.25,
    "generationtime_ms": 0.33,
    "utc_offset_seconds": 0,
    "timezone": "GMT",
    "hourly_units": {
        "time": "iso8601",
        "wave_height": "m",
        "wave_period": "s",
        "wave_direction": "°",
        "swell_wave_height": "m",
    },
    "hourly": {
        "time": ["2026-09-18T00:00", "2026-09-18T01:00", "2026-09-18T02:00"],
        "wave_height": [0.48, 0.5, None],
        "wave_period": [6.2, 6.3, 6.1],
        "wave_direction": [135, 137, 140],
        "swell_wave_height": [0.4, 0.42, 0.41],
    },
}


def mock_fetcher(handler) -> HttpFetcher:
    """An HttpFetcher backed by a MockTransport, so no socket is opened."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return HttpFetcher(client=client)


class TestOpenMeteoAdapter:
    def _fetcher(self, payload=OPEN_METEO_PAYLOAD, status=200) -> HttpFetcher:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=payload)

        return mock_fetcher(handler)

    async def test_parses_records_with_canonical_units(self) -> None:
        adapter = OpenMeteoMarineAdapter(fetcher=self._fetcher())
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
        )

        result = await adapter.fetch(request)

        waves = result.records_for(MarineVariable.SIGNIFICANT_WAVE_HEIGHT)
        assert len(waves) == 3
        assert waves[0].value == 0.48
        assert waves[0].unit == "m"
        assert waves[0].kind is MeasurementKind.FORECAST

    async def test_upstream_degree_symbol_maps_to_canonical_unit(self) -> None:
        """Open-Meteo reports '°'; the record contract requires 'degree'."""
        adapter = OpenMeteoMarineAdapter(fetcher=self._fetcher())
        request = FetchRequest(
            bbox=BOX, time_window=WINDOW, variables=frozenset({MarineVariable.MEAN_WAVE_DIRECTION})
        )

        result = await adapter.fetch(request)

        assert result.records[0].unit == "degree"

    async def test_null_value_becomes_a_missing_record_not_a_zero(self) -> None:
        """A gap must stay a gap: 0.0 m would read as a flat calm sea."""
        adapter = OpenMeteoMarineAdapter(fetcher=self._fetcher())
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
        )

        result = await adapter.fetch(request)

        missing = [r for r in result.records if r.quality is DataQuality.MISSING]
        assert len(missing) == 1
        assert missing[0].value is None

    async def test_coordinates_come_from_the_response_not_the_request(self) -> None:
        """Provenance must record the grid point served, not the point requested."""
        adapter = OpenMeteoMarineAdapter(fetcher=self._fetcher())
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
        )

        result = await adapter.fetch(request)

        assert result.records[0].lat == 13.125
        assert result.records[0].lon == 80.25

    async def test_unexpected_unit_is_refused_rather_than_converted_by_guess(self) -> None:
        payload = json.loads(json.dumps(OPEN_METEO_PAYLOAD))
        payload["hourly_units"]["wave_height"] = "ft"
        adapter = OpenMeteoMarineAdapter(fetcher=self._fetcher(payload))
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
        )

        result = await adapter.fetch(request)

        assert result.records_for(MarineVariable.SIGNIFICANT_WAVE_HEIGHT) == ()
        assert any("not the expected" in w for w in result.warnings)
        assert MarineVariable.SIGNIFICANT_WAVE_HEIGHT in result.missing_variables

    async def test_http_error_raises_source_unavailable(self) -> None:
        adapter = OpenMeteoMarineAdapter(fetcher=self._fetcher(payload={"error": True}, status=503))
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
        )

        with pytest.raises(SourceUnavailableError):
            await adapter.fetch(request)

    async def test_raw_payload_is_archived(self) -> None:
        from orca_ingest.archive import NullArchive

        adapter = OpenMeteoMarineAdapter(fetcher=self._fetcher(), archive=NullArchive())
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
        )

        result = await adapter.fetch(request)

        assert len(result.raw_refs) == 1
        assert result.raw_refs[0].sha256


class TestIncoisErddapAdapter:
    """Dimension order is discovered, never assumed."""

    def _metadata(self, **overrides) -> DatasetMetadata:
        defaults = {
            "dataset_id": "incois_tmi_3day_datasets",
            "dimensions": ("time", "latitude", "longitude"),
            "variables": ("SST", "WSPD_LF"),
            "license": "INCOIS open data",
            "time_coverage_start": datetime(1997, 12, 7, tzinfo=UTC),
            "time_coverage_end": datetime(2014, 12, 31, tzinfo=UTC),
            "lon_min": 0.0,
            "lon_max": 359.75,
        }
        return DatasetMetadata(**(defaults | overrides))

    def test_zlev_dimension_is_pinned_not_dropped(self) -> None:
        """NOAA_AVHRR_AMSR_datasets really has a zlev axis; dropping it misaligns all
        following constraints onto the wrong dimension."""
        meta = self._metadata(dimensions=("time", "zlev", "latitude", "longitude"))
        adapter = IncoisErddapAdapter(dataset_id="NOAA_AVHRR_AMSR_datasets", metadata=meta)
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SEA_SURFACE_TEMPERATURE}),
        )

        url, _ = adapter._build_query(meta, "SST", request)

        assert "[0]" in url
        assert url.index("[0]") < url.index("(12.9)")

    def test_0_360_longitude_grid_is_converted(self) -> None:
        """A 0..360 grid would silently return nothing for a negative longitude."""
        meta = self._metadata()
        adapter = IncoisErddapAdapter(dataset_id="incois_tmi_3day_datasets", metadata=meta)
        western = BoundingBox(min_lat=5, max_lat=10, min_lon=-70, max_lon=-60)
        request = FetchRequest(
            bbox=western,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SEA_SURFACE_TEMPERATURE}),
        )

        url, _ = adapter._build_query(meta, "SST", request)

        assert "(290" in url and "(300" in url

    async def test_window_beyond_coverage_is_refused_with_an_explanation(self) -> None:
        """This dataset's coverage ends in 2014; a 2026 query must say so, not return nothing
        mysteriously."""
        adapter = IncoisErddapAdapter(
            dataset_id="incois_tmi_3day_datasets", metadata=self._metadata()
        )
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SEA_SURFACE_TEMPERATURE}),
        )

        result = await adapter.fetch(request)

        assert result.is_empty
        assert any("historical archive" in w for w in result.warnings)
        assert any("2014-12-31" in w for w in result.warnings)

    async def test_csv_is_parsed_into_records(self) -> None:
        csv_body = (
            "time,latitude,longitude,SST\n"
            "UTC,degrees_north,degrees_east,degree_C\n"
            "2014-12-30T00:00:00Z,13.125,80.25,28.7\n"
            "2014-12-30T00:00:00Z,13.375,80.25,NaN\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=csv_body, headers={"content-type": "text/csv"})

        adapter = IncoisErddapAdapter(
            dataset_id="incois_tmi_3day_datasets",
            metadata=self._metadata(),
            fetcher=mock_fetcher(handler),
        )
        request = FetchRequest(
            bbox=BOX,
            time_window=TimeWindow(
                start=datetime(2014, 12, 29, tzinfo=UTC), end=datetime(2014, 12, 31, tzinfo=UTC)
            ),
            variables=frozenset({MarineVariable.SEA_SURFACE_TEMPERATURE}),
        )

        result = await adapter.fetch(request)

        assert len(result.records) == 2
        assert result.records[0].value == 28.7
        assert result.records[0].unit == "degC"
        assert result.records[1].quality is DataQuality.MISSING

    async def test_archive_records_are_issued_at_their_valid_time(self) -> None:
        """Using retrieval time would make a 2014 measurement look fresh to the gate."""
        csv_body = (
            "time,latitude,longitude,SST\n"
            "UTC,degrees_north,degrees_east,degree_C\n"
            "2014-12-30T00:00:00Z,13.125,80.25,28.7\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=csv_body, headers={"content-type": "text/csv"})

        adapter = IncoisErddapAdapter(
            dataset_id="incois_tmi_3day_datasets",
            metadata=self._metadata(),
            fetcher=mock_fetcher(handler),
        )
        request = FetchRequest(
            bbox=BOX,
            time_window=TimeWindow(
                start=datetime(2014, 12, 29, tzinfo=UTC), end=datetime(2014, 12, 31, tzinfo=UTC)
            ),
            variables=frozenset({MarineVariable.SEA_SURFACE_TEMPERATURE}),
        )

        result = await adapter.fetch(request)

        record = result.records[0]
        assert record.issued_time == record.valid_time
        assert record.issued_time.year == 2014

    async def test_404_is_no_data_not_an_outage(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found: Your query produced no matching results")

        adapter = IncoisErddapAdapter(
            dataset_id="incois_tmi_3day_datasets",
            metadata=self._metadata(),
            fetcher=mock_fetcher(handler),
        )
        request = FetchRequest(
            bbox=BOX,
            time_window=TimeWindow(
                start=datetime(2014, 12, 29, tzinfo=UTC), end=datetime(2014, 12, 31, tzinfo=UTC)
            ),
            variables=frozenset({MarineVariable.SEA_SURFACE_TEMPERATURE}),
        )

        result = await adapter.fetch(request)  # must not raise

        assert result.is_empty
        assert any("no data" in w for w in result.warnings)


class TestCredentialedScaffolds:
    """Gated sources must fail loudly and specifically, never fabricate."""

    @pytest.mark.parametrize(
        ("adapter_cls", "env_var"),
        [
            (ImdAdapter, "IMD_API_KEY"),
            (CmemsAdapter, "CMEMS_USERNAME"),
            (MosdacAdapter, "MOSDAC_USERNAME"),
            (NiotOmniBuoyAdapter, "NIOT_OMNI_PORTAL_TOKEN"),
        ],
    )
    async def test_missing_credentials_named_in_the_error(self, adapter_cls, env_var) -> None:
        adapter = adapter_cls(env={})
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset(
                {MarineVariable.WIND_SPEED, MarineVariable.SIGNIFICANT_WAVE_HEIGHT}
            ),
        )

        with pytest.raises(SourceUnavailableError) as excinfo:
            await adapter.fetch(request)

        assert env_var in str(excinfo.value)
        assert adapter.missing_credentials()

    async def test_present_credentials_give_a_different_reason(self) -> None:
        """'Not registered' and 'not built yet' need different actions from the team."""
        adapter = CmemsAdapter(env={"CMEMS_USERNAME": "u", "CMEMS_PASSWORD": "p"})
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
        )

        with pytest.raises(SourceUnavailableError, match="not implemented"):
            await adapter.fetch(request)

    @pytest.mark.parametrize("adapter_cls", [IncoisPfzAdapter, IncoisOsfAdapter])
    async def test_bulletin_sources_explain_they_have_no_api(self, adapter_cls) -> None:
        adapter = adapter_cls(env={})
        request = FetchRequest(
            bbox=BOX,
            time_window=WINDOW,
            variables=frozenset({MarineVariable.SIGNIFICANT_WAVE_HEIGHT}),
        )

        with pytest.raises(SourceUnavailableError) as excinfo:
            await adapter.fetch(request)

        assert "not built" in str(excinfo.value)

    def test_mosdac_does_not_claim_oceansat3_sst(self) -> None:
        """EOS-06 SSTM is non-operational; claiming it would fail an ISRO jury."""
        assert not MosdacAdapter(env={}).descriptor.serves(MarineVariable.SEA_SURFACE_TEMPERATURE)

    def test_pfz_cadence_reflects_three_issues_per_week(self) -> None:
        assert IncoisPfzAdapter(env={}).descriptor.cadence.period == timedelta(hours=56)

    def test_gated_sources_declare_they_need_auth(self) -> None:
        for adapter_cls in (ImdAdapter, CmemsAdapter, MosdacAdapter, NiotOmniBuoyAdapter):
            assert adapter_cls(env={}).descriptor.requires_auth
