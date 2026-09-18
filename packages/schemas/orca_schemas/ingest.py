"""Ingestion contracts (PLAN.md Phase 1.1).

Every data source — INCOIS ERDDAP, IMD, CMEMS, Open-Meteo, MOSDAC, OMNI buoys — is an
adapter that emits the same record shape, so the rest of ORCA never learns which upstream
a number came from except through its provenance fields. That uniformity is what makes
the degradation chain of DEPLOYMENT.md §4 possible (INCOIS SST -> NOAA_AVHRR_AMSR ->
CMEMS thetao) without conditional logic scattered through the reasoning layer.

Three invariants are enforced here rather than trusted:

* **Canonical units.** A record's unit must be the canonical unit for its variable. A
  source reporting wave height in centimetres must convert inside its adapter, not hand
  ORCA a number that a safety threshold will later misread as metres.
* **Missing data is representable.** ``value`` may be ``None`` only when quality is
  ``missing``. Absent data is normal operation, not an error (ARCHITECTURE.md §7).
* **Time is absolute.** Naive datetimes are rejected; staleness decisions depend on
  unambiguous instants.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from orca_schemas.base import OrcaModel


class MarineVariable(StrEnum):
    """Canonical variable names.

    Adapters translate their upstream naming (``SST``, ``analysed_sst``, ``thetao``,
    ``VHM0``) into these, which is what lets one variable be served by several sources
    that use different vocabularies.
    """

    SEA_SURFACE_TEMPERATURE = "sea_surface_temperature"
    CHLOROPHYLL = "chlorophyll"
    SIGNIFICANT_WAVE_HEIGHT = "significant_wave_height"
    PEAK_WAVE_PERIOD = "peak_wave_period"
    MEAN_WAVE_DIRECTION = "mean_wave_direction"
    SWELL_HEIGHT = "swell_height"
    WIND_SPEED = "wind_speed"
    WIND_DIRECTION = "wind_direction"
    CURRENT_SPEED = "current_speed"
    CURRENT_DIRECTION = "current_direction"
    SEA_SURFACE_SALINITY = "sea_surface_salinity"
    MIXED_LAYER_DEPTH = "mixed_layer_depth"
    DEPTH_OF_20C_ISOTHERM = "depth_of_20c_isotherm"
    SEA_LEVEL = "sea_level"


CANONICAL_UNITS: dict[MarineVariable, str] = {
    MarineVariable.SEA_SURFACE_TEMPERATURE: "degC",
    MarineVariable.CHLOROPHYLL: "mg m-3",
    MarineVariable.SIGNIFICANT_WAVE_HEIGHT: "m",
    MarineVariable.PEAK_WAVE_PERIOD: "s",
    MarineVariable.MEAN_WAVE_DIRECTION: "degree",
    MarineVariable.SWELL_HEIGHT: "m",
    MarineVariable.WIND_SPEED: "m s-1",
    MarineVariable.WIND_DIRECTION: "degree",
    MarineVariable.CURRENT_SPEED: "m s-1",
    MarineVariable.CURRENT_DIRECTION: "degree",
    MarineVariable.SEA_SURFACE_SALINITY: "1e-3",
    MarineVariable.MIXED_LAYER_DEPTH: "m",
    MarineVariable.DEPTH_OF_20C_ISOTHERM: "m",
    MarineVariable.SEA_LEVEL: "m",
}
"""The one unit each variable is allowed to be expressed in (CF-style unit names)."""


class DataQuality(StrEnum):
    """How much the value itself can be relied on, independent of its age."""

    OBSERVED = "observed"
    INTERPOLATED = "interpolated"
    GAP_FILLED = "gap_filled"
    SUSPECT = "suspect"
    MISSING = "missing"


class MeasurementKind(StrEnum):
    """Whether the number describes the past or the future.

    The reliability layer (PLAN.md Phase 10.1) backtests *forecasts* against buoy
    *observations*; without this distinction on the record, the two cannot be paired.
    """

    OBSERVATION = "observation"
    ANALYSIS = "analysis"
    FORECAST = "forecast"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must be timezone-aware"
        raise ValueError(msg)
    return value


class BoundingBox(OrcaModel):
    """Geographic query extent in WGS84 degrees."""

    min_lat: float = Field(ge=-90, le=90)
    max_lat: float = Field(ge=-90, le=90)
    min_lon: float = Field(ge=-180, le=180)
    max_lon: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def _check_ordering(self) -> Self:
        if self.min_lat > self.max_lat:
            msg = "min_lat must not exceed max_lat"
            raise ValueError(msg)
        if self.min_lon > self.max_lon:
            msg = "min_lon must not exceed max_lon"
            raise ValueError(msg)
        return self

    def contains(self, lat: float, lon: float) -> bool:
        """Whether a point falls inside this box (inclusive edges)."""
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon

    def intersects(self, other: BoundingBox) -> bool:
        """Whether two boxes overlap, used to check that a source covers a query."""
        return not (
            self.max_lat < other.min_lat
            or other.max_lat < self.min_lat
            or self.max_lon < other.min_lon
            or other.max_lon < self.min_lon
        )


class TimeWindow(OrcaModel):
    """Half-open time extent ``[start, end)`` for a query."""

    start: datetime
    end: datetime

    _aware = field_validator("start", "end")(_require_aware)

    @model_validator(mode="after")
    def _check_ordering(self) -> Self:
        if self.start >= self.end:
            msg = "start must be strictly before end"
            raise ValueError(msg)
        return self

    def contains(self, moment: datetime) -> bool:
        """Whether an instant falls in ``[start, end)``."""
        return self.start <= _require_aware(moment) < self.end


class Cadence(OrcaModel):
    """How often a source refreshes, and how much lateness is tolerated.

    Staleness is a deterministic function of cadence and the clock — never a judgement
    call, and never something an LLM decides.
    """

    period: timedelta = Field(description="Nominal interval between issues.")
    grace: timedelta = Field(
        default=timedelta(0),
        description="Lateness tolerated beyond one period before data counts as stale.",
    )

    @field_validator("period")
    @classmethod
    def _positive_period(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            msg = "cadence period must be positive"
            raise ValueError(msg)
        return value

    @field_validator("grace")
    @classmethod
    def _non_negative_grace(cls, value: timedelta) -> timedelta:
        if value < timedelta(0):
            msg = "cadence grace must not be negative"
            raise ValueError(msg)
        return value

    @property
    def deadline(self) -> timedelta:
        """Maximum age before data issued on this cadence counts as stale."""
        return self.period + self.grace

    def is_stale(self, issued_time: datetime, now: datetime) -> bool:
        """Whether data issued at ``issued_time`` is stale as of ``now``."""
        return (_require_aware(now) - _require_aware(issued_time)) > self.deadline


class SourceDescriptor(OrcaModel):
    """Registry metadata describing what one source can serve.

    The planner consults these to choose a source instead of calling a hard-wired
    endpoint (PLAN.md G3 / Phase 3.1); the degradation chain uses ``authority_rank`` to
    decide what to fall back to.
    """

    source_id: str = Field(description="Stable identifier, e.g. 'incois_erddap'.")
    name: str
    authority_rank: int = Field(
        ge=1,
        description="1 = authoritative Indian agency; higher = further down the fallback chain.",
    )
    variables: frozenset[MarineVariable]
    coverage: BoundingBox
    cadence: Cadence
    license: str
    requires_auth: bool = False
    attribution: str | None = Field(
        default=None, description="Text the UI must display when this source is used."
    )

    def serves(self, variable: MarineVariable) -> bool:
        """Whether this source publishes the given variable."""
        return variable in self.variables


class ObservationRecord(OrcaModel):
    """The common record every adapter emits (PLAN.md Phase 1.1).

    Carries its own provenance — which source, issued when, valid when, under what
    licence — so a number can always be traced back without consulting the adapter that
    produced it.
    """

    variable: MarineVariable
    value: float | None = Field(description="None only when quality is 'missing'.")
    unit: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    depth_m: float | None = Field(default=None, ge=0, description="Positive down; None = surface.")
    valid_time: datetime = Field(description="Instant the value describes.")
    issued_time: datetime = Field(description="Instant the source published it.")
    source: str = Field(description="The SourceDescriptor.source_id that produced this record.")
    dataset_id: str | None = Field(
        default=None, description="Upstream dataset identifier, e.g. an ERDDAP griddap id."
    )
    quality: DataQuality = DataQuality.OBSERVED
    kind: MeasurementKind = MeasurementKind.OBSERVATION
    license: str

    _aware = field_validator("valid_time", "issued_time")(_require_aware)

    @model_validator(mode="after")
    def _check_value_and_unit(self) -> Self:
        if self.quality is DataQuality.MISSING:
            if self.value is not None:
                msg = "records with quality 'missing' must not carry a value"
                raise ValueError(msg)
        else:
            if self.value is None:
                msg = f"value may be None only when quality is 'missing', got {self.quality}"
                raise ValueError(msg)
            if not math.isfinite(self.value):
                msg = f"value must be finite, got {self.value}"
                raise ValueError(msg)

        expected = CANONICAL_UNITS[self.variable]
        if self.unit != expected:
            msg = (
                f"{self.variable} must be expressed in {expected!r}, got {self.unit!r}; "
                "convert inside the adapter"
            )
            raise ValueError(msg)
        return self

    def age(self, now: datetime) -> timedelta:
        """How long ago this record was issued."""
        return _require_aware(now) - self.issued_time

    def is_stale(self, cadence: Cadence, now: datetime) -> bool:
        """Whether this record is stale for the given cadence."""
        return cadence.is_stale(self.issued_time, now)
