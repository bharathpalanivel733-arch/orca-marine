"""The environmental picture a tick evaluates against (PLAN.md Phase 8.1).

Everything the alert rules read arrives through this module, and everything here carries an
``issued_time``. That is not bookkeeping: a cyclone position from nine hours ago is not a
cyclone position, and a rule that fired on one would be warning about a storm that has
since moved a hundred kilometres.

**Geometry is evaluated exactly, not approximated by a bounding box.** IMD publishes
`cyclone_wind` as GeoJSON wind radii (27/34/50/64 kt) and `cyclone_cou` as a cone of
uncertainty, both genuinely non-convex. A bounding-box test would put boats inside a
warning they are not in, and — worse in the other direction — the cone narrows towards the
present position, so a box around it would look reassuring exactly where the storm is.

Ray casting is used rather than shapely so the rule layer stays a pure, auditable
calculation. Polygons here are small (tens of vertices), and the implementation is short
enough to read in full, which matters more than speed for something that decides whether to
wake a fisherman.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# Knots at which each IMD wind-radius ring is defined. Ordered weakest to strongest so a
# containment test can report the strongest ring a vessel falls inside.
WIND_RADIUS_KNOTS: tuple[int, ...] = (27, 34, 50, 64)


class HazardSource(StrEnum):
    """Who published a hazard. Used for attribution in the CAP message.

    ORCA never issues a cyclone warning of its own authorship; it relays IMD's and says so.
    Attribution is a legal and a trust requirement, not a courtesy.
    """

    IMD = "imd"
    INCOIS = "incois"
    NDMA = "ndma"
    ORCA = "orca"


@dataclass(frozen=True)
class GeoPolygon:
    """A closed ring in (lat, lon) order.

    (lat, lon) rather than GeoJSON's (lon, lat) because every other ORCA surface uses that
    order, and a silently transposed pair puts a boat in the wrong hemisphere. Conversion
    happens once, at the adapter boundary, in :meth:`from_geojson`.
    """

    points: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            msg = f"a polygon needs at least 3 points, got {len(self.points)}"
            raise ValueError(msg)

    @classmethod
    def from_geojson(cls, ring: list[list[float]]) -> GeoPolygon:
        """Build from a GeoJSON linear ring, which is (lon, lat)."""
        points = tuple((float(pair[1]), float(pair[0])) for pair in ring)
        # GeoJSON rings repeat the first point last; ray casting closes the ring itself.
        if len(points) > 3 and points[0] == points[-1]:
            points = points[:-1]
        return cls(points)

    def contains(self, lat: float, lon: float) -> bool:
        """Ray casting: count crossings of a ray cast east from the point.

        A point on the boundary counts as inside. For a warning polygon that is the correct
        bias — being exactly on the 64-knot radius is not a reason to withhold the warning.
        """
        inside = False
        count = len(self.points)
        for index in range(count):
            lat1, lon1 = self.points[index]
            lat2, lon2 = self.points[(index + 1) % count]
            if (lat1 > lat) != (lat2 > lat):
                # Longitude where the edge crosses this latitude.
                crossing_lon = lon1 + (lat - lat1) / (lat2 - lat1) * (lon2 - lon1)
                if lon < crossing_lon:
                    inside = not inside
                elif lon == crossing_lon:
                    return True
        return inside

    def to_cap_polygon(self) -> str:
        """CAP 1.2 ``<polygon>``: space-separated ``lat,lon`` pairs, first repeated last."""
        pairs = [f"{lat:.4f},{lon:.4f}" for lat, lon in self.points]
        pairs.append(pairs[0])
        return " ".join(pairs)


@dataclass(frozen=True)
class WindRadius:
    """One IMD wind-radius ring."""

    knots: int
    polygon: GeoPolygon


@dataclass(frozen=True)
class CycloneSystem:
    """A tropical system as IMD publishes it.

    ``cone_of_uncertainty`` is the forecast track envelope (`cyclone_cou`). A vessel inside
    the cone is not necessarily in the wind yet — it is in the range of positions the storm
    may take — so the two containments produce different severities rather than one.
    """

    system_id: str
    name: str
    centre_lat: float
    centre_lon: float
    issued_time: datetime
    source: HazardSource = HazardSource.IMD
    movement_bearing_deg: float | None = None
    movement_speed_kmh: float | None = None
    max_sustained_wind_knots: float | None = None
    wind_radii: tuple[WindRadius, ...] = ()
    cone_of_uncertainty: GeoPolygon | None = None

    def age(self, now: datetime) -> timedelta:
        return now - self.issued_time

    def strongest_ring_containing(self, lat: float, lon: float) -> int | None:
        """The highest wind-radius ring the point falls inside, in knots.

        Rings nest, so the strongest containing ring is the one that describes the
        conditions — reporting the weakest would understate a vessel in the eyewall.
        """
        hits = [r.knots for r in self.wind_radii if r.polygon.contains(lat, lon)]
        return max(hits) if hits else None

    def in_cone(self, lat: float, lon: float) -> bool:
        return self.cone_of_uncertainty is not None and self.cone_of_uncertainty.contains(
            lat, lon
        )


@dataclass(frozen=True)
class MarineConditions:
    """The sea state at one vessel's position, with the age of every driver.

    Optional fields are genuinely optional: lightning and squall feeds are not always
    available, and their absence must widen uncertainty rather than silently read as "no
    lightning". A rule that cannot see its driver reports itself unevaluated, never safe.
    """

    lat: float
    lon: float
    issued_time: datetime
    significant_wave_height_m: float | None = None
    wind_speed_ms: float | None = None
    swell_period_s: float | None = None
    lightning_strikes_within_25km: int | None = None
    squall_probability: float | None = None
    current_east_ms: float = 0.0
    current_north_ms: float = 0.0
    source: HazardSource = HazardSource.INCOIS

    def age(self, now: datetime) -> timedelta:
        return now - self.issued_time


@dataclass(frozen=True)
class HazardPicture:
    """Everything one tick knows about the world.

    Assembled once per tick and passed to every subscriber's evaluation, so two boats in the
    same place on the same tick cannot receive contradictory warnings.
    """

    observed_at: datetime
    cyclones: tuple[CycloneSystem, ...] = ()
    # Per-position conditions, keyed by the subscriber they were fetched for. Keyed rather
    # than gridded because Phase 8 consumes whatever the ingestion layer returns; the
    # gridded path belongs to the ingest cache, not here.
    conditions: dict[str, MarineConditions] | None = None

    def conditions_for(self, user_id: str) -> MarineConditions | None:
        return (self.conditions or {}).get(user_id)

    def active_cyclones(self, *, max_age: timedelta = timedelta(hours=6)) -> tuple[
        CycloneSystem, ...
    ]:
        """Cyclones whose published position is recent enough to act on.

        Six hours matches IMD's bulletin cadence for a system under watch. An older
        position describes where the storm *was*, and warning on it would misplace the
        hazard by roughly one bulletin's worth of movement.
        """
        return tuple(c for c in self.cyclones if c.age(self.observed_at) <= max_age)
