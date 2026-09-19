"""Tool and dataset registry (PLAN.md Phase 3.1, closing gap G3).

The planner must never contain a line like ``if need_waves: call_cmems()``. It asks this
registry which datasets can serve a variable, over a box, at a time, within a budget —
and the registry answers from declared capability metadata. That indirection is what
"autonomous dataset discovery" means concretely, and it is why adding a new source later
is a registry entry rather than a change to the planner.

Selection is **deterministic and explainable**. Candidates are filtered on hard
constraints (does it serve the variable, cover the box, cover the instant, fit the
budget) and then ordered by `(authority_rank, -reliability_prior, cost, dataset_id)`.
Every rejection is reported with its reason, so a plan can show why INCOIS was not used
rather than leaving the jury to wonder.

The entries here encode findings that were verified live in earlier phases rather than
assumptions: the INCOIS ERDDAP datasets are archives with real end dates, IMD now needs a
key, and CMEMS is the primary wave source because INCOIS publishes no wave griddap.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from orca_schemas import BoundingBox, Cadence, DatasetCapability, MarineVariable

INDIAN_OCEAN = BoundingBox(min_lat=-10, max_lat=30, min_lon=45, max_lon=100)
GLOBAL = BoundingBox(min_lat=-90, max_lat=90, min_lon=-180, max_lon=180)


class RejectionReason(StrEnum):
    """Why a dataset was not selected."""

    VARIABLE_NOT_SERVED = "variable_not_served"
    OUTSIDE_COVERAGE = "outside_coverage"
    OUTSIDE_TEMPORAL_COVERAGE = "outside_temporal_coverage"
    OVER_BUDGET = "over_budget"
    REQUIRES_AUTH = "requires_auth"


@dataclass(frozen=True)
class Rejection:
    """One dataset that was considered and set aside."""

    dataset_id: str
    reason: RejectionReason
    detail: str


@dataclass(frozen=True)
class Selection:
    """The registry's answer: what to use, and what was ruled out and why."""

    selected: tuple[DatasetCapability, ...]
    rejected: tuple[Rejection, ...]

    @property
    def best(self) -> DatasetCapability | None:
        return self.selected[0] if self.selected else None

    def explain(self) -> str:
        """One line for the provenance panel and the planner's trace."""
        chosen = ", ".join(d.dataset_id for d in self.selected) or "none"
        ruled_out = ", ".join(f"{r.dataset_id}({r.reason})" for r in self.rejected) or "none"
        return f"selected: {chosen}; rejected: {ruled_out}"


def _capability(**kwargs: object) -> DatasetCapability:
    return DatasetCapability(**kwargs)  # type: ignore[arg-type]


# Findings encoded here were verified live in Phases 0.4 and 1 — see docs/PROGRESS.md.
DEFAULT_CAPABILITIES: tuple[DatasetCapability, ...] = (
    _capability(
        dataset_id="open_meteo_marine",
        source_id="open_meteo_marine",
        name="Open-Meteo Marine forecast",
        variables=frozenset(
            {
                MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
                MarineVariable.PEAK_WAVE_PERIOD,
                MarineVariable.MEAN_WAVE_DIRECTION,
                MarineVariable.SWELL_HEIGHT,
            }
        ),
        coverage_bbox=GLOBAL,
        cadence=Cadence(period=timedelta(hours=3), grace=timedelta(hours=1)),
        latency=timedelta(hours=1),
        authority_rank=4,
        reliability_prior=0.6,
        cost=0.0,
        license="CC-BY-4.0 (Open-Meteo)",
        requires_auth=False,
        notes="Keyless and live-verified. Currently the only keyless numeric source.",
    ),
    _capability(
        dataset_id="cmems_mod_glo_wav_anfc",
        source_id="cmems",
        name="CMEMS global wave analysis and forecast",
        variables=frozenset(
            {
                MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
                MarineVariable.PEAK_WAVE_PERIOD,
                MarineVariable.MEAN_WAVE_DIRECTION,
                MarineVariable.SWELL_HEIGHT,
            }
        ),
        coverage_bbox=GLOBAL,
        cadence=Cadence(period=timedelta(hours=12), grace=timedelta(hours=4)),
        latency=timedelta(hours=6),
        authority_rank=2,
        reliability_prior=0.8,
        cost=1.0,
        license="Copernicus Marine Service licence",
        requires_auth=True,
        notes=(
            "PRIMARY wave source: Phase 0.4 spike (b) established INCOIS publishes no "
            "searchable wave/OSF griddap dataset. Dataset ids rotate ~biannually."
        ),
    ),
    _capability(
        dataset_id="incois_tmi_3day_datasets",
        source_id="incois_erddap",
        name="INCOIS TMI 3-day SST (archive)",
        variables=frozenset({MarineVariable.SEA_SURFACE_TEMPERATURE}),
        coverage_bbox=GLOBAL,
        cadence=Cadence(period=timedelta(days=1), grace=timedelta(days=2)),
        latency=timedelta(days=1),
        authority_rank=1,
        reliability_prior=0.85,
        cost=0.0,
        license="INCOIS open data",
        requires_auth=False,
        temporal_coverage_start=datetime(1997, 12, 7, tzinfo=UTC),
        temporal_coverage_end=datetime(2014, 12, 31, tzinfo=UTC),
        notes=(
            "ARCHIVE, verified 2026-09-18: coverage ends 2014-12-31. Authoritative but "
            "useless for a present-day question; valuable for the Phase 10.1 hindcast."
        ),
    ),
    _capability(
        dataset_id="incois_oceansat2_datasets",
        source_id="incois_erddap",
        name="INCOIS Oceansat-2 OCM chlorophyll (archive)",
        variables=frozenset({MarineVariable.CHLOROPHYLL}),
        coverage_bbox=GLOBAL,
        cadence=Cadence(period=timedelta(days=1), grace=timedelta(days=2)),
        latency=timedelta(days=3),
        authority_rank=1,
        reliability_prior=0.75,
        cost=0.0,
        license="INCOIS open data",
        requires_auth=False,
        temporal_coverage_start=datetime(2011, 2, 2, tzinfo=UTC),
        temporal_coverage_end=datetime(2020, 5, 1, tzinfo=UTC),
        notes="ARCHIVE, verified 2026-09-18: coverage ends 2020-05-01.",
    ),
    _capability(
        dataset_id="imd_marine_bulletins",
        source_id="imd",
        name="IMD marine bulletins, warnings and cyclone tracks",
        variables=frozenset({MarineVariable.WIND_SPEED, MarineVariable.WIND_DIRECTION}),
        coverage_bbox=INDIAN_OCEAN,
        cadence=Cadence(period=timedelta(hours=6), grace=timedelta(hours=2)),
        latency=timedelta(hours=1),
        authority_rank=1,
        reliability_prior=0.9,
        cost=1.0,
        license="IMD terms; attribution required",
        requires_auth=True,
        notes="Verified 2026-09-18: now returns 401 without IMD_API_KEY.",
    ),
    _capability(
        dataset_id="niot_omni_buoys",
        source_id="niot_omni",
        name="NIOT OMNI buoy observations",
        variables=frozenset(
            {
                MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
                MarineVariable.SEA_SURFACE_TEMPERATURE,
                MarineVariable.WIND_SPEED,
                MarineVariable.WIND_DIRECTION,
            }
        ),
        coverage_bbox=INDIAN_OCEAN,
        cadence=Cadence(period=timedelta(hours=1), grace=timedelta(hours=3)),
        latency=timedelta(hours=1),
        authority_rank=1,
        reliability_prior=0.95,
        cost=1.0,
        license="INCOIS/NIOT open-data policy (2018)",
        requires_auth=True,
        notes="In-situ truth for the reliability layer. Portal access not yet granted.",
    ),
)


class DatasetRegistry:
    """Capability lookup the planner selects from."""

    def __init__(self, capabilities: Iterable[DatasetCapability] = DEFAULT_CAPABILITIES) -> None:
        self._capabilities = tuple(capabilities)
        duplicates = len(self._capabilities) - len({c.dataset_id for c in self._capabilities})
        if duplicates:
            msg = "dataset_id must be unique within a registry"
            raise ValueError(msg)

    def all(self) -> tuple[DatasetCapability, ...]:
        return self._capabilities

    def get(self, dataset_id: str) -> DatasetCapability:
        for capability in self._capabilities:
            if capability.dataset_id == dataset_id:
                return capability
        known = ", ".join(sorted(c.dataset_id for c in self._capabilities))
        msg = f"unknown dataset {dataset_id!r}; registered: {known}"
        raise KeyError(msg)

    def register(self, capability: DatasetCapability) -> None:
        if any(c.dataset_id == capability.dataset_id for c in self._capabilities):
            msg = f"dataset {capability.dataset_id!r} is already registered"
            raise ValueError(msg)
        self._capabilities = (*self._capabilities, capability)

    def select(
        self,
        *,
        variable: MarineVariable,
        bbox: BoundingBox,
        at: datetime,
        max_cost: float | None = None,
        allow_auth_required: bool = True,
        available_credentials: frozenset[str] = frozenset(),
    ) -> Selection:
        """Choose datasets for one variable, reporting every rejection.

        ``available_credentials`` holds the source ids whose credentials are actually
        configured. A dataset needing auth that is not in that set is rejected with a
        stated reason rather than selected and failed later — the planner should not
        spend a step on a call that cannot succeed.
        """
        selected: list[DatasetCapability] = []
        rejected: list[Rejection] = []

        for capability in self._capabilities:
            if not capability.serves(variable):
                rejected.append(
                    Rejection(
                        capability.dataset_id,
                        RejectionReason.VARIABLE_NOT_SERVED,
                        f"does not publish {variable}",
                    )
                )
                continue
            if not capability.coverage_bbox.intersects(bbox):
                rejected.append(
                    Rejection(
                        capability.dataset_id,
                        RejectionReason.OUTSIDE_COVERAGE,
                        "coverage does not overlap the requested box",
                    )
                )
                continue
            if not capability.covers_time(at):
                end = capability.temporal_coverage_end
                rejected.append(
                    Rejection(
                        capability.dataset_id,
                        RejectionReason.OUTSIDE_TEMPORAL_COVERAGE,
                        (f"archive ending {end.date()}" if end else "starts after the request")
                        + f"; cannot serve {at.date()}",
                    )
                )
                continue
            if max_cost is not None and capability.cost > max_cost:
                rejected.append(
                    Rejection(
                        capability.dataset_id,
                        RejectionReason.OVER_BUDGET,
                        f"cost {capability.cost} exceeds budget {max_cost}",
                    )
                )
                continue
            if capability.requires_auth and (
                not allow_auth_required or capability.source_id not in available_credentials
            ):
                rejected.append(
                    Rejection(
                        capability.dataset_id,
                        RejectionReason.REQUIRES_AUTH,
                        f"needs credentials for {capability.source_id}, which are not configured",
                    )
                )
                continue
            selected.append(capability)

        selected.sort(key=lambda c: (c.authority_rank, -c.reliability_prior, c.cost, c.dataset_id))
        return Selection(selected=tuple(selected), rejected=tuple(rejected))
