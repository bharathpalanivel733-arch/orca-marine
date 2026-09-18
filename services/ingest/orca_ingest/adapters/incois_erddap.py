"""INCOIS ERDDAP adapter (PLAN.md Phase 1.2).

**Nothing about a dataset is hardcoded.** Dimension names, their order, variable names,
the licence and the time coverage are all read from the dataset's own ``info`` document
before a single data request is built. That is not fastidiousness: live inspection of
this server found that ``NOAA_AVHRR_AMSR_datasets`` carries a ``zlev`` dimension and
``ascat_daily_datasets`` a ``depth`` dimension, so the naive
``SST[time][latitude][longitude]`` subset pattern from the project docs would have
produced a malformed griddap query against exactly the datasets it named.

**Live finding, recorded here because it changes how ORCA uses this source.** Every
dataset on this server is a historical archive, not a live feed (coverage verified
2026-09-18):

===============================  ======================
dataset                          time coverage ends
===============================  ======================
``incois_tmi_3day_datasets``     2014-12-31
``NOAA_AVHRR_AMSR_datasets``     2011-10-04
``incois_argo_sst_weekly``       2010-12-29
``AMSRE_MONTHLY_GLOBAL``         2011-09-14
``incois_oceansat2_datasets``    2020-05-01
``ascat_daily_datasets``         2023-05-21
``ascat_mnt_datasets``           2021-11-01
``incois_quickscat_daily_...``   2009-11-21
``Indian_ARGO_Floats``           2025-04-23
===============================  ======================

So INCOIS ERDDAP cannot answer "is it safe tomorrow". Its real value to ORCA is the
**hindcast archive** the reliability layer needs (PLAN.md Phase 10.1), plus climatology
for the causal engine. The adapter therefore declares an honest cadence: records are
tagged with the dataset's actual time coverage, and the degradation policy will reject
them for present-day queries on staleness grounds automatically. That is the system
working correctly, not a bug to paper over.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from orca_schemas import (
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

SOURCE_ID = "incois_erddap"
DEFAULT_BASE_URL = "https://erddap.incois.gov.in/erddap"

# Upstream variable name -> (canonical variable, upstream unit). Populated only for
# variables whose dataset has been inspected live. A dataset not listed here is not
# guessed at: it is reported as unserved.
VARIABLE_ALIASES: dict[str, tuple[MarineVariable, str]] = {
    "SST": (MarineVariable.SEA_SURFACE_TEMPERATURE, "degC"),
    "sst": (MarineVariable.SEA_SURFACE_TEMPERATURE, "degC"),
    "analysed_sst": (MarineVariable.SEA_SURFACE_TEMPERATURE, "degC"),
    "chlor_a": (MarineVariable.CHLOROPHYLL, "mg m-3"),
    "chlorophyll": (MarineVariable.CHLOROPHYLL, "mg m-3"),
    "wind_speed": (MarineVariable.WIND_SPEED, "m s-1"),
    "WSPD_LF": (MarineVariable.WIND_SPEED, "m s-1"),
}


@dataclass(frozen=True)
class DatasetMetadata:
    """What the server says about a dataset, discovered rather than assumed."""

    dataset_id: str
    dimensions: tuple[str, ...]
    variables: tuple[str, ...]
    license: str
    time_coverage_start: datetime | None
    time_coverage_end: datetime | None
    lon_min: float | None
    lon_max: float | None

    @property
    def uses_0_360_longitude(self) -> bool:
        """Whether the grid is indexed 0..360 rather than -180..180.

        ``incois_tmi_3day_datasets`` is 0..359.75. Requesting lon=80 works either way,
        but a request for the Arabian Sea at lon=-70 would silently return nothing on a
        0..360 grid, so the adapter converts rather than guesses.
        """
        return self.lon_max is not None and self.lon_max > 180


class IncoisErddapAdapter(SourceAdapter):
    """Historical gridded and tabular oceanography from the INCOIS ERDDAP server."""

    def __init__(
        self,
        *,
        dataset_id: str,
        base_url: str = DEFAULT_BASE_URL,
        fetcher: HttpFetcher | None = None,
        archive: RawPayloadArchive | None = None,
        metadata: DatasetMetadata | None = None,
    ) -> None:
        self._dataset_id = dataset_id
        self._base_url = base_url.rstrip("/")
        self._fetcher = fetcher
        self._archive = archive or NullArchive()
        self._metadata = metadata

    @property
    def descriptor(self) -> SourceDescriptor:
        meta = self._metadata
        served: frozenset[MarineVariable] = frozenset()
        license_text = "INCOIS ERDDAP; see dataset metadata"
        if meta is not None:
            served = frozenset(
                VARIABLE_ALIASES[name][0] for name in meta.variables if name in VARIABLE_ALIASES
            )
            license_text = meta.license

        return SourceDescriptor(
            source_id=SOURCE_ID,
            name=f"INCOIS ERDDAP ({self._dataset_id})",
            authority_rank=1,
            variables=served,
            coverage=BoundingBox(min_lat=-90, max_lat=90, min_lon=-180, max_lon=180),
            # These are archives. The cadence is declared as the dataset's own nominal
            # spacing, which means present-day queries will correctly find the records
            # stale instead of treating a 2014 SST as current.
            cadence=Cadence(period=timedelta(days=1), grace=timedelta(days=2)),
            license=license_text,
            requires_auth=False,
            attribution="Indian National Centre for Ocean Information Services (INCOIS)",
        )

    async def discover(self) -> DatasetMetadata:
        """Read dimensions, variables, licence and coverage from the server.

        Called before any data request. Results are cached per adapter instance, since a
        dataset's structure does not change between queries.
        """
        if self._metadata is not None:
            return self._metadata

        url = f"{self._base_url}/info/{self._dataset_id}/index.json"
        fetcher = self._fetcher or HttpFetcher()
        async with fetcher as client:
            try:
                response = await client.get(url)
            except HttpError as exc:
                raise SourceUnavailableError(SOURCE_ID, f"info request failed: {exc}") from exc

        if not response.ok:
            raise SourceUnavailableError(
                SOURCE_ID, f"info for {self._dataset_id}: HTTP {response.status_code}"
            )

        rows = response.json()["table"]["rows"]
        dimensions = tuple(row[1] for row in rows if row[0] == "dimension")
        variables = tuple(row[1] for row in rows if row[0] == "variable")
        attributes = {row[2]: row[4] for row in rows if row[1] == "NC_GLOBAL"}

        def parse_time(key: str) -> datetime | None:
            raw = attributes.get(key)
            if not raw:
                return None
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return None

        def parse_float(key: str) -> float | None:
            raw = attributes.get(key)
            try:
                return float(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None

        self._metadata = DatasetMetadata(
            dataset_id=self._dataset_id,
            dimensions=dimensions,
            variables=variables,
            license=str(attributes.get("license", "unspecified"))[:200],
            time_coverage_start=parse_time("time_coverage_start"),
            time_coverage_end=parse_time("time_coverage_end"),
            lon_min=parse_float("geospatial_lon_min"),
            lon_max=parse_float("geospatial_lon_max"),
        )
        return self._metadata

    async def fetch(self, request: FetchRequest) -> FetchResult:
        meta = await self.discover()
        warnings: list[str] = []

        target = self._select_variable(meta, request)
        if target is None:
            return FetchResult(
                source_id=SOURCE_ID,
                retrieved_at=datetime.now(UTC),
                missing_variables=frozenset(request.variables),
                warnings=(
                    f"{self._dataset_id} publishes {meta.variables!r}, none of which maps to "
                    f"the requested variables",
                ),
            )
        upstream_name, variable, unit = target

        # Refuse out-of-coverage windows rather than sending a query the server will
        # reject, and say what the dataset actually covers.
        if meta.time_coverage_end and request.time_window.start > meta.time_coverage_end:
            return FetchResult(
                source_id=SOURCE_ID,
                retrieved_at=datetime.now(UTC),
                missing_variables=frozenset(request.variables),
                warnings=(
                    f"{self._dataset_id} coverage ends {meta.time_coverage_end.date()}, before "
                    f"the requested window starting {request.time_window.start.date()}; this is "
                    "a historical archive, not a live feed",
                ),
            )

        url, params = self._build_query(meta, upstream_name, request)
        fetcher = self._fetcher or HttpFetcher()
        async with fetcher as client:
            try:
                response = await client.get(url, params=params)
            except HttpError as exc:
                raise SourceUnavailableError(SOURCE_ID, str(exc)) from exc

        if response.status_code == 404:
            # ERDDAP answers "no matching data" with 404, which is an empty result, not
            # an outage. Conflating the two would trigger a pointless fallback.
            return FetchResult(
                source_id=SOURCE_ID,
                retrieved_at=response.retrieved_at,
                missing_variables=frozenset(request.variables),
                warnings=(f"{self._dataset_id}: no data for this box and window",),
            )
        if not response.ok:
            raise SourceUnavailableError(
                SOURCE_ID, f"HTTP {response.status_code}: {response.text[:200]}"
            )

        ref = self._archive.store(
            response.content,
            source_id=SOURCE_ID,
            content_type=response.content_type,
            retrieved_at=response.retrieved_at,
            dataset_id=self._dataset_id,
        )

        records = self._parse_csv(
            response.text, upstream_name, variable, unit, meta, response.retrieved_at, warnings
        )
        return FetchResult(
            source_id=SOURCE_ID,
            records=tuple(records),
            retrieved_at=response.retrieved_at,
            missing_variables=frozenset(v for v in request.variables if v is not variable),
            warnings=tuple(warnings),
            raw_refs=(ref,),
        )

    def _select_variable(
        self, meta: DatasetMetadata, request: FetchRequest
    ) -> tuple[str, MarineVariable, str] | None:
        for upstream_name in meta.variables:
            mapping = VARIABLE_ALIASES.get(upstream_name)
            if mapping and mapping[0] in request.variables:
                return upstream_name, mapping[0], mapping[1]
        return None

    def _build_query(
        self, meta: DatasetMetadata, upstream_name: str, request: FetchRequest
    ) -> tuple[str, dict[str, str]]:
        """Build a griddap subset using the dataset's *discovered* dimension order.

        Every dimension the server reports gets a constraint, in the order the server
        reports it. Dimensions ORCA has no opinion about — ``zlev``, ``depth`` — are
        pinned to their first index rather than omitted, because omitting one shifts
        every following constraint onto the wrong axis.
        """
        min_lon, max_lon = request.bbox.min_lon, request.bbox.max_lon
        if meta.uses_0_360_longitude:
            min_lon = min_lon % 360
            max_lon = max_lon % 360

        constraints: list[str] = []
        for dimension in meta.dimensions:
            lowered = dimension.lower()
            if lowered == "time":
                start = request.time_window.start.strftime("%Y-%m-%dT%H:%M:%SZ")
                end = request.time_window.end.strftime("%Y-%m-%dT%H:%M:%SZ")
                constraints.append(f"[({start}):1:({end})]")
            elif lowered in {"latitude", "lat"}:
                constraints.append(f"[({request.bbox.min_lat}):1:({request.bbox.max_lat})]")
            elif lowered in {"longitude", "lon"}:
                constraints.append(f"[({min_lon}):1:({max_lon})]")
            else:
                # zlev / depth and anything else discovered: take the first index.
                constraints.append("[0]")

        query = f"{upstream_name}{''.join(constraints)}"
        return f"{self._base_url}/griddap/{self._dataset_id}.csv?{query}", {}

    def _parse_csv(
        self,
        body: str,
        upstream_name: str,
        variable: MarineVariable,
        unit: str,
        meta: DatasetMetadata,
        retrieved_at: datetime,
        warnings: list[str],
    ) -> list[ObservationRecord]:
        """Parse ERDDAP CSV: header row, then a units row, then data."""
        reader = csv.reader(io.StringIO(body))
        try:
            header = next(reader)
            units_row = next(reader)
        except StopIteration:
            warnings.append("empty CSV response")
            return []

        columns = {name.strip(): index for index, name in enumerate(header)}
        required = ("time", upstream_name)
        missing_columns = [c for c in required if c not in columns]
        if missing_columns:
            warnings.append(f"response missing column(s): {', '.join(missing_columns)}")
            return []

        lat_col = next((c for c in ("latitude", "lat") if c in columns), None)
        lon_col = next((c for c in ("longitude", "lon") if c in columns), None)
        if lat_col is None or lon_col is None:
            warnings.append("response has no latitude/longitude columns")
            return []

        reported_unit = units_row[columns[upstream_name]].strip() if units_row else ""
        if reported_unit and reported_unit not in {unit, "degree_C", "degrees_C", "Celsius"}:
            warnings.append(
                f"{upstream_name}: upstream unit {reported_unit!r} differs from the expected "
                f"{unit!r}; records skipped rather than converted by guess"
            )
            return []

        records: list[ObservationRecord] = []
        for row in reader:
            if not row or len(row) < len(header):
                continue
            raw_value = row[columns[upstream_name]].strip()
            try:
                valid_time = datetime.fromisoformat(
                    row[columns["time"]].strip().replace("Z", "+00:00")
                )
                lat = float(row[columns[lat_col]])
                lon = float(row[columns[lon_col]])
            except (ValueError, IndexError):
                continue

            if lon > 180:  # normalise a 0..360 grid back to the canonical range
                lon -= 360

            if raw_value in {"", "NaN", "nan"}:
                records.append(
                    ObservationRecord(
                        variable=variable,
                        value=None,
                        unit=unit,
                        lat=lat,
                        lon=lon,
                        valid_time=valid_time,
                        issued_time=valid_time,
                        source=SOURCE_ID,
                        dataset_id=self._dataset_id,
                        quality=DataQuality.MISSING,
                        kind=MeasurementKind.OBSERVATION,
                        license=meta.license,
                    )
                )
                continue

            records.append(
                ObservationRecord(
                    variable=variable,
                    value=float(raw_value),
                    unit=unit,
                    lat=lat,
                    lon=lon,
                    valid_time=valid_time,
                    # An archive's issue time is its valid time: there is no later
                    # revision, and using retrieval time would make decade-old data
                    # look fresh to the staleness check.
                    issued_time=valid_time,
                    source=SOURCE_ID,
                    dataset_id=self._dataset_id,
                    quality=DataQuality.OBSERVED,
                    kind=MeasurementKind.OBSERVATION,
                    license=meta.license,
                )
            )
        return records
