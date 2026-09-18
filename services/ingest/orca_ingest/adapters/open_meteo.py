"""Open-Meteo Marine adapter (PLAN.md Phase 1.5).

Keyless, live, and — as of the Phase 0.4 spikes and this phase's live verification — the
only numeric marine source ORCA can reach today without credentials. DEPLOYMENT.md calls
it the "fastest demo-safe fallback"; in practice it is currently carrying more weight
than that, because INCOIS ERDDAP turns out to publish only historical archives and IMD
now requires an API key. It is still ranked below the Indian authorities in
``authority_rank``, so it yields to them the moment they can serve live data.

Everything this adapter emits is a *forecast* (``MeasurementKind.FORECAST``), which
matters downstream: the reliability layer scores forecasts against observations, and
conflating the two would corrupt that scoring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from orca_schemas import (
    ArchiveRef,
    BoundingBox,
    Cadence,
    DataQuality,
    MarineVariable,
    MeasurementKind,
    ObservationRecord,
    SourceDescriptor,
)

from orca_ingest.adapter import FetchRequest, FetchResult, SourceAdapter, SourceUnavailableError
from orca_ingest.archive import NullArchive, RawPayloadArchive
from orca_ingest.http import HttpError, HttpFetcher

SOURCE_ID = "open_meteo_marine"
DEFAULT_BASE_URL = "https://marine-api.open-meteo.com/v1/marine"

# Upstream field -> (canonical variable, upstream unit as documented by hourly_units).
# The unit is asserted against the response rather than assumed: if Open-Meteo ever
# switches to feet, the record validator must reject it instead of publishing a wave
# height that a safety threshold reads as metres.
FIELD_MAP: dict[str, tuple[MarineVariable, str]] = {
    "wave_height": (MarineVariable.SIGNIFICANT_WAVE_HEIGHT, "m"),
    "wave_period": (MarineVariable.PEAK_WAVE_PERIOD, "s"),
    "wave_direction": (MarineVariable.MEAN_WAVE_DIRECTION, "°"),
    "swell_wave_height": (MarineVariable.SWELL_HEIGHT, "m"),
}

VARIABLE_TO_FIELD = {variable: field for field, (variable, _) in FIELD_MAP.items()}

# Open-Meteo reports direction in "°"; ORCA's canonical unit name is "degree".
UNIT_ALIASES = {"°": "degree", "deg": "degree"}


class OpenMeteoMarineAdapter(SourceAdapter):
    """Wave and swell forecasts from the Open-Meteo Marine API."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        fetcher: HttpFetcher | None = None,
        archive: RawPayloadArchive | None = None,
    ) -> None:
        self._base_url = base_url
        self._fetcher = fetcher
        self._archive = archive or NullArchive()

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id=SOURCE_ID,
            name="Open-Meteo Marine API",
            # Rank 4: below INCOIS (1), IMD (1) and CMEMS (2). It is a fallback by
            # design even while it is the most available source in practice.
            authority_rank=4,
            variables=frozenset(VARIABLE_TO_FIELD),
            coverage=BoundingBox(min_lat=-90, max_lat=90, min_lon=-180, max_lon=180),
            # Open-Meteo refreshes its marine fields roughly hourly.
            cadence=Cadence(period=timedelta(hours=3), grace=timedelta(hours=1)),
            license="CC-BY-4.0 (Open-Meteo; underlying model data per provider)",
            requires_auth=False,
            attribution="Weather data by Open-Meteo.com",
        )

    async def fetch(self, request: FetchRequest) -> FetchResult:
        fields = [VARIABLE_TO_FIELD[v] for v in sorted(request.variables) if v in VARIABLE_TO_FIELD]
        unsupported = self.unsupported_variables(request)
        if not fields:
            return FetchResult(
                source_id=SOURCE_ID,
                retrieved_at=datetime.now(UTC),
                missing_variables=unsupported,
                warnings=("no requested variable is served by Open-Meteo Marine",),
            )

        # Open-Meteo is a point API, so the box is queried at its centre. The record's
        # lat/lon are the coordinates Open-Meteo echoes back (its nearest grid point),
        # never the coordinates we asked for — provenance must reflect reality.
        centre_lat = (request.bbox.min_lat + request.bbox.max_lat) / 2
        centre_lon = (request.bbox.min_lon + request.bbox.max_lon) / 2

        params = {
            "latitude": f"{centre_lat:.4f}",
            "longitude": f"{centre_lon:.4f}",
            "hourly": ",".join(fields),
            "timezone": "UTC",
            "start_date": request.time_window.start.date().isoformat(),
            "end_date": (request.time_window.end - timedelta(seconds=1)).date().isoformat(),
        }

        fetcher = self._fetcher or HttpFetcher()
        async with fetcher as client:
            try:
                response = await client.get(self._base_url, params=params)
            except HttpError as exc:
                raise SourceUnavailableError(SOURCE_ID, str(exc)) from exc

        if not response.ok:
            raise SourceUnavailableError(
                SOURCE_ID, f"HTTP {response.status_code}: {response.text[:200]}"
            )

        ref = self._archive.store(
            response.content,
            source_id=SOURCE_ID,
            content_type=response.content_type,
            retrieved_at=response.retrieved_at,
            dataset_id="marine",
        )
        return self._parse(response.json(), request, response.retrieved_at, ref, unsupported)

    def _parse(
        self,
        payload: dict[str, Any],
        request: FetchRequest,
        retrieved_at: datetime,
        ref: ArchiveRef,
        unsupported: frozenset[MarineVariable],
    ) -> FetchResult:
        # NOTE on issued_time: Open-Meteo does not publish the model's issue time in
        # this response, so retrieval time is used. That is a conservative choice — the
        # data can only be older than when we fetched it, never newer — and it means
        # staleness is measured against our fetch rather than the model run. The adapter
        # contract prefers an upstream-provided issue time; this source cannot give one,
        # and that limitation is recorded here rather than hidden.
        hourly = payload.get("hourly") or {}
        units = payload.get("hourly_units") or {}
        times = hourly.get("time") or []
        lat = float(payload["latitude"])
        lon = float(payload["longitude"])

        records: list[ObservationRecord] = []
        warnings: list[str] = []
        served: set[MarineVariable] = set()

        for field, (variable, expected_unit) in FIELD_MAP.items():
            if variable not in request.variables or field not in hourly:
                continue

            reported_unit = str(units.get(field, expected_unit))
            canonical_unit = UNIT_ALIASES.get(reported_unit, reported_unit)
            expected_canonical = UNIT_ALIASES.get(expected_unit, expected_unit)
            if canonical_unit != expected_canonical:
                # Do not attempt a conversion we have not been asked for: refuse the
                # field and say why, rather than guess a scale factor.
                warnings.append(
                    f"{field}: upstream unit {reported_unit!r} is not the expected "
                    f"{expected_unit!r}; field skipped"
                )
                continue

            values = hourly.get(field) or []
            for timestamp, value in zip(times, values, strict=False):
                valid_time = datetime.fromisoformat(timestamp).replace(tzinfo=UTC)
                if not request.time_window.contains(valid_time):
                    continue
                if value is None:
                    records.append(
                        ObservationRecord(
                            variable=variable,
                            value=None,
                            unit=canonical_unit,
                            lat=lat,
                            lon=lon,
                            valid_time=valid_time,
                            issued_time=retrieved_at,
                            source=SOURCE_ID,
                            dataset_id="marine",
                            quality=DataQuality.MISSING,
                            kind=MeasurementKind.FORECAST,
                            license=self.descriptor.license,
                        )
                    )
                    continue
                records.append(
                    ObservationRecord(
                        variable=variable,
                        value=float(value),
                        unit=canonical_unit,
                        lat=lat,
                        lon=lon,
                        valid_time=valid_time,
                        issued_time=retrieved_at,
                        source=SOURCE_ID,
                        dataset_id="marine",
                        quality=DataQuality.OBSERVED,
                        kind=MeasurementKind.FORECAST,
                        license=self.descriptor.license,
                    )
                )
                served.add(variable)

        missing = unsupported | frozenset(
            v for v in request.variables if v in VARIABLE_TO_FIELD and v not in served
        )
        return FetchResult(
            source_id=SOURCE_ID,
            records=tuple(records),
            retrieved_at=retrieved_at,
            missing_variables=missing,
            warnings=tuple(warnings),
            raw_refs=(ref,),
        )
