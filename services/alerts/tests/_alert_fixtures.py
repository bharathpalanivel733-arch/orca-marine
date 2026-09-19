"""Shared fixtures for the alert subsystem tests.

One fixed clock and one fixed vessel throughout. Alert logic is almost entirely about time
— suppression windows, staleness, time-to-boundary — so a test that used the wall clock
would be flaky in exactly the places that matter most.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from orca_kernels import VesselClass, VesselProfile
from orca_speech import Language

from orca_alerts import (
    ChannelEndpoint,
    CycloneSystem,
    DeliveryChannelKind,
    GeoPolygon,
    HazardPicture,
    MarineConditions,
    Subscriber,
    TriggerId,
    TriggerOutcome,
    TriggerResult,
    VesselState,
    WindRadius,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)

# A point in the Palk Bay, on the Indian side and well clear of the boundary.
PALK_BAY_LAT = 9.20
PALK_BAY_LON = 79.35


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def vallam() -> VesselProfile:
    """An open FRP vallam: the most exposed class ORCA models, and the demo's boat."""
    return VesselProfile(
        vessel_id="TN-RMD-0142",
        vessel_class=VesselClass.FRP_VALLAM,
        length_overall_m=8.0,
        engine_power_hp=9.9,
        range_nm=25,
        freeboard_m=0.6,
        crew_size=3,
        home_port="Rameswaram",
    )


@pytest.fixture
def trawler() -> VesselProfile:
    """A decked mechanized trawler, for the contrast the rules are built around."""
    return VesselProfile(
        vessel_id="TN-TUT-0009",
        vessel_class=VesselClass.MECHANIZED_TRAWLER,
        length_overall_m=19.0,
        engine_power_hp=120,
        range_nm=180,
        freeboard_m=2.1,
        crew_size=8,
        home_port="Thoothukudi",
    )


@pytest.fixture
def subscriber(vallam: VesselProfile) -> Subscriber:
    return Subscriber(
        user_id="u-rmd-1",
        vessel=vallam,
        language=Language.TAMIL,
        state=VesselState(
            lat=PALK_BAY_LAT,
            lon=PALK_BAY_LON,
            reported_at=NOW - timedelta(minutes=2),
            heading_deg=120.0,
            speed_knots=3.0,
        ),
        endpoints=(ChannelEndpoint(DeliveryChannelKind.IN_APP, "u-rmd-1"),),
        home_harbour="Rameswaram",
    )


def conditions(
    *,
    wave_m: float | None = 1.0,
    wind_ms: float | None = 5.0,
    strikes: int | None = 0,
    squall: float | None = 0.05,
    age: timedelta = timedelta(hours=1),
    lat: float = PALK_BAY_LAT,
    lon: float = PALK_BAY_LON,
    relative_to: datetime = NOW,
) -> MarineConditions:
    """Marine conditions with everything defaulting to a calm, recent forecast.

    ``relative_to`` exists for multi-tick tests: a forecast whose issue time stayed pinned
    to the first tick goes stale by the third, and the test would silently end up exercising
    the staleness rule instead of whatever it meant to.
    """
    return MarineConditions(
        lat=lat,
        lon=lon,
        issued_time=relative_to - age,
        significant_wave_height_m=wave_m,
        wind_speed_ms=wind_ms,
        lightning_strikes_within_25km=strikes,
        squall_probability=squall,
    )


def square(centre_lat: float, centre_lon: float, half_degrees: float) -> GeoPolygon:
    """A square polygon, for containment tests where the shape is not the point."""
    return GeoPolygon(
        (
            (centre_lat - half_degrees, centre_lon - half_degrees),
            (centre_lat - half_degrees, centre_lon + half_degrees),
            (centre_lat + half_degrees, centre_lon + half_degrees),
            (centre_lat + half_degrees, centre_lon - half_degrees),
        )
    )


def cyclone(
    *,
    name: str = "Fengal",
    rings: tuple[tuple[int, float], ...] = ((27, 2.0), (34, 1.0), (50, 0.5), (64, 0.2)),
    cone_half_degrees: float | None = 3.0,
    age: timedelta = timedelta(hours=1),
    centre_lat: float = PALK_BAY_LAT,
    centre_lon: float = PALK_BAY_LON,
) -> CycloneSystem:
    """A cyclone with nested wind radii centred on the test position.

    Nested by construction — each stronger ring is smaller — because that is how IMD
    publishes them and what the "strongest containing ring" logic depends on.
    """
    return CycloneSystem(
        system_id=f"sys-{name.lower()}",
        name=name,
        centre_lat=centre_lat,
        centre_lon=centre_lon,
        issued_time=NOW - age,
        movement_bearing_deg=315.0,
        movement_speed_kmh=12.0,
        max_sustained_wind_knots=70.0,
        wind_radii=tuple(
            WindRadius(knots=knots, polygon=square(centre_lat, centre_lon, half))
            for knots, half in rings
        ),
        cone_of_uncertainty=(
            square(centre_lat, centre_lon, cone_half_degrees)
            if cone_half_degrees is not None
            else None
        ),
    )


def picture(**kwargs: object) -> HazardPicture:
    """A hazard picture with no cyclones and no conditions unless asked for."""
    return HazardPicture(observed_at=NOW, **kwargs)  # type: ignore[arg-type]


def result(
    trigger_id: TriggerId = TriggerId.WAVE_HEIGHT,
    outcome: TriggerOutcome = TriggerOutcome.WARNING,
    **observed: object,
) -> TriggerResult:
    """A trigger result, for testing the layers above the rules."""
    return TriggerResult(
        trigger_id=trigger_id,
        outcome=outcome,
        detail="synthetic result for testing",
        observed=dict(observed),
        threshold={"avoid_m": 2.0},
        threshold_source="test",
    )
