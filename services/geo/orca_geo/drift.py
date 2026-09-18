"""Predictive drift and time-to-boundary (PLAN.md Phase 2.4).

The Tier-1 differentiator: warn **before** the crossing, not after. GEMINI warns about
weather; nothing in service warns a fisherman that his current heading and the surface
current put him across the IMBL in forty minutes.

The motion model is deliberately simple and stated plainly, because a safety warning
built on an unexplainable model is not defensible to a jury:

    effective velocity = vessel velocity (heading, speed through water)
                       + surface current vector (from the forecast)

A vessel with engines off and nets out has zero vessel velocity, so it drifts purely with
the current. That is exactly the situation in which Palk Strait crossings happen, and it
is why speed zero is a supported input rather than an edge case.

Positions are advanced along geodesics on WGS84. The crossing time is found by stepping
forward and then **bisecting** the step in which the side flips, so the answer does not
depend on the step size beyond the stated tolerance.

What this does not model: wind leeway, tidal streams varying along the track, engine
response, or the helmsman changing course. The forecast current is sampled once and held
constant over the horizon. These limits are real and are reported alongside the result
rather than buried.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from pyproj import Geod

from orca_geo.geofence import BoundarySide, ImblGeofence, WarningBand

WGS84 = Geod(ellps="WGS84")

KNOTS_TO_MS = 0.514444
DEFAULT_HORIZON = timedelta(hours=1)
DEFAULT_STEP = timedelta(minutes=1)
BISECTION_TOLERANCE_SECONDS = 5.0


@dataclass(frozen=True)
class VesselMotion:
    """A vessel's motion through the water, plus the current carrying it."""

    heading_deg: float
    speed_ms: float
    current_east_ms: float = 0.0
    current_north_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.speed_ms < 0:
            msg = "speed must not be negative"
            raise ValueError(msg)
        if not 0 <= self.heading_deg < 360:
            msg = f"heading must be in [0, 360), got {self.heading_deg}"
            raise ValueError(msg)

    @classmethod
    def from_knots(
        cls,
        heading_deg: float,
        speed_knots: float,
        current_east_ms: float = 0.0,
        current_north_ms: float = 0.0,
    ) -> VesselMotion:
        """Build from speed in knots, which is what a fisherman's instruments show."""
        return cls(
            heading_deg=heading_deg,
            speed_ms=speed_knots * KNOTS_TO_MS,
            current_east_ms=current_east_ms,
            current_north_ms=current_north_ms,
        )

    @property
    def effective_velocity_ms(self) -> tuple[float, float]:
        """(east, north) velocity in m/s, vessel plus current."""
        heading_rad = math.radians(self.heading_deg)
        east = self.speed_ms * math.sin(heading_rad) + self.current_east_ms
        north = self.speed_ms * math.cos(heading_rad) + self.current_north_ms
        return (east, north)

    @property
    def effective_speed_ms(self) -> float:
        east, north = self.effective_velocity_ms
        return math.hypot(east, north)

    @property
    def effective_course_deg(self) -> float:
        """Course over ground in degrees from true north.

        This is what actually matters and what differs from the compass heading — the
        gap between the two is the drift the fisherman cannot see.
        """
        east, north = self.effective_velocity_ms
        if east == 0 and north == 0:
            return self.heading_deg
        return math.degrees(math.atan2(east, north)) % 360.0


@dataclass(frozen=True)
class ProjectedPosition:
    """Where the vessel is expected to be after some elapsed time."""

    elapsed: timedelta
    lat: float
    lon: float
    distance_metres: float
    side: BoundarySide
    band: WarningBand


@dataclass(frozen=True)
class DriftForecast:
    """The result of projecting a vessel forward against the boundary."""

    track: tuple[ProjectedPosition, ...]
    time_to_boundary: timedelta | None
    crossing_lat: float | None
    crossing_lon: float | None
    horizon: timedelta
    assumptions: tuple[str, ...]

    @property
    def will_cross(self) -> bool:
        """Whether a crossing is predicted inside the horizon."""
        return self.time_to_boundary is not None

    @property
    def closest_approach(self) -> ProjectedPosition:
        """The point of the track nearest the boundary."""
        return min(self.track, key=lambda p: p.distance_metres)


def advance(lat: float, lon: float, course_deg: float, distance_m: float) -> tuple[float, float]:
    """Move a position along a geodesic. Returns (lat, lon)."""
    lon2, lat2, _ = WGS84.fwd(lon, lat, course_deg, distance_m)
    return (lat2, lon2)


class DriftPredictor:
    """Projects a vessel forward and reports when it would cross the boundary."""

    def __init__(self, geofence: ImblGeofence | None = None) -> None:
        self._geofence = geofence or ImblGeofence()

    def project(
        self,
        lat: float,
        lon: float,
        motion: VesselMotion,
        *,
        horizon: timedelta = DEFAULT_HORIZON,
        step: timedelta = DEFAULT_STEP,
        home_side: BoundarySide | None = None,
    ) -> DriftForecast:
        """Project the vessel forward over ``horizon``, sampling every ``step``.

        ``home_side`` defaults to the side the vessel starts on, which is the sane
        assumption: a boat fishing in Indian waters is warned about leaving them.
        """
        if step.total_seconds() <= 0:
            msg = "step must be positive"
            raise ValueError(msg)
        if horizon.total_seconds() <= 0:
            msg = "horizon must be positive"
            raise ValueError(msg)

        start_side = self._geofence.side_of(lat, lon)
        side_to_keep = home_side or start_side

        course = motion.effective_course_deg
        speed = motion.effective_speed_ms

        track: list[ProjectedPosition] = []
        crossing_time: timedelta | None = None
        crossing_position: tuple[float, float] | None = None

        elapsed_seconds = 0.0
        horizon_seconds = horizon.total_seconds()
        step_seconds = step.total_seconds()
        previous = (lat, lon, 0.0)

        while elapsed_seconds <= horizon_seconds:
            moved = speed * elapsed_seconds
            point_lat, point_lon = advance(lat, lon, course, moved) if moved else (lat, lon)
            proximity = self._geofence.proximity(point_lat, point_lon, home_side=side_to_keep)
            track.append(
                ProjectedPosition(
                    elapsed=timedelta(seconds=elapsed_seconds),
                    lat=point_lat,
                    lon=point_lon,
                    distance_metres=proximity.distance_metres,
                    side=proximity.side,
                    band=proximity.band,
                )
            )

            if crossing_time is None and proximity.side is not side_to_keep:
                seconds = self._bisect_crossing(
                    lat, lon, course, speed, previous[2], elapsed_seconds, side_to_keep
                )
                crossing_time = timedelta(seconds=seconds)
                crossing_position = advance(lat, lon, course, speed * seconds)

            previous = (point_lat, point_lon, elapsed_seconds)
            elapsed_seconds += step_seconds

        assumptions = (
            "surface current sampled once and held constant over the horizon",
            "no wind leeway, tidal variation along track, or course change modelled",
            f"positions advanced along WGS84 geodesics, bisected to "
            f"±{BISECTION_TOLERANCE_SECONDS:.0f}s",
        )
        return DriftForecast(
            track=tuple(track),
            time_to_boundary=crossing_time,
            crossing_lat=crossing_position[0] if crossing_position else None,
            crossing_lon=crossing_position[1] if crossing_position else None,
            horizon=horizon,
            assumptions=assumptions,
        )

    def _bisect_crossing(
        self,
        lat: float,
        lon: float,
        course: float,
        speed: float,
        before_seconds: float,
        after_seconds: float,
        side_to_keep: BoundarySide,
    ) -> float:
        """Refine the crossing instant inside the step where the side flipped.

        Makes the answer independent of step size: a 1-minute and a 10-second step must
        agree, which the tests assert.
        """
        low, high = before_seconds, after_seconds
        while high - low > BISECTION_TOLERANCE_SECONDS:
            middle = (low + high) / 2
            point_lat, point_lon = advance(lat, lon, course, speed * middle)
            if self._geofence.side_of(point_lat, point_lon) is side_to_keep:
                low = middle
            else:
                high = middle
        return high
