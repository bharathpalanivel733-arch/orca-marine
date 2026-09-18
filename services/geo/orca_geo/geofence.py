"""Geofencing against the IMBL and other restricted waters (PLAN.md Phase 2.2-2.3).

Everything here is deterministic geometry. No model, no heuristic, no LLM: a distance to
a maritime boundary is arithmetic on an ellipsoid, and a fisherman's liberty depends on
it being computed the same way every time.

Distances are **geodesic on WGS84** via ``pyproj.Geod`` — not planar distance in degrees,
which would be wrong by a latitude-dependent factor and would quietly under-warn.
PostGIS ``ST_Distance`` on the ``geography`` type is the authoritative second
implementation (see ``storage.py``). The stack tests cross-check the two: they agree to a
few metres (the nearest point is found in planar degree space, and the two differ
slightly in spheroid handling) and are required never to disagree about which warning
band a position falls in.

Warning bands default to the values in METHODS.md §1 — **5 km amber, 2 km red** — and are
configurable, because different vessel classes and different boundaries justify different
margins.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pyproj import Geod
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

from orca_geo.imbl import imbl_positions, imbl_provenance

WGS84: Final = Geod(ellps="WGS84")

DEFAULT_AMBER_METRES: Final = 5_000.0
DEFAULT_RED_METRES: Final = 2_000.0


class WarningBand(StrEnum):
    """How close to a boundary a vessel is."""

    CLEAR = "clear"
    AMBER = "amber"
    RED = "red"
    CROSSED = "crossed"


class BoundarySide(StrEnum):
    """Which side of the IMBL a position falls on.

    Determined by the sign of the cross product against the nearest boundary segment,
    with the boundary traversed north-east (Bay of Bengal) to south-west (Point T). It is
    a geometric fact about the line, not a political judgement: the labels simply name
    the state whose waters lie on that side.
    """

    INDIA = "india"
    SRI_LANKA = "sri_lanka"


@dataclass(frozen=True)
class BandThresholds:
    """Configurable warning distances."""

    amber_metres: float = DEFAULT_AMBER_METRES
    red_metres: float = DEFAULT_RED_METRES

    def __post_init__(self) -> None:
        if self.red_metres <= 0 or self.amber_metres <= 0:
            msg = "warning distances must be positive"
            raise ValueError(msg)
        if self.red_metres >= self.amber_metres:
            msg = (
                f"red band ({self.red_metres} m) must be closer than amber ({self.amber_metres} m)"
            )
            raise ValueError(msg)

    def band_for(self, distance_metres: float, *, crossed: bool = False) -> WarningBand:
        """Classify a distance. ``crossed`` overrides everything."""
        if crossed:
            return WarningBand.CROSSED
        if distance_metres <= self.red_metres:
            return WarningBand.RED
        if distance_metres <= self.amber_metres:
            return WarningBand.AMBER
        return WarningBand.CLEAR


@dataclass(frozen=True)
class BoundaryProximity:
    """Where a vessel stands relative to the IMBL."""

    lat: float
    lon: float
    distance_metres: float
    side: BoundarySide
    band: WarningBand
    nearest_lat: float
    nearest_lon: float
    bearing_to_boundary_deg: float

    @property
    def distance_km(self) -> float:
        return self.distance_metres / 1000.0


def _boundary_linestring() -> LineString:
    """The IMBL in shapely (lon, lat) order."""
    return LineString([p.lonlat for p in imbl_positions()])


def geodesic_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Geodesic distance in metres on WGS84."""
    _, _, distance = WGS84.inv(lon1, lat1, lon2, lat2)
    return float(distance)


def geodesic_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing in degrees from true north, normalised to [0, 360)."""
    forward, _, _ = WGS84.inv(lon1, lat1, lon2, lat2)
    return float(forward % 360.0)


class ImblGeofence:
    """Proximity, side and warning band against the agreed India-Sri Lanka IMBL."""

    def __init__(self, thresholds: BandThresholds | None = None) -> None:
        self._line = _boundary_linestring()
        self._thresholds = thresholds or BandThresholds()

    @property
    def thresholds(self) -> BandThresholds:
        return self._thresholds

    @property
    def provenance(self) -> dict[str, object]:
        """Treaty provenance, attached to every warning this object produces."""
        return imbl_provenance()

    def nearest_point(self, lat: float, lon: float) -> tuple[float, float]:
        """Closest point on the boundary, as (lat, lon).

        Shapely finds the nearest point in degree space, then the distance to it is
        measured geodesically. Over the few kilometres that matter for a warning band
        the degree-space nearest point is within centimetres of the true geodesic
        nearest point, and erring here would only ever shift the chosen point *along*
        the boundary, not across it.
        """
        vessel = Point(lon, lat)
        on_line, _ = nearest_points(self._line, vessel)
        return (on_line.y, on_line.x)

    def side_of(self, lat: float, lon: float) -> BoundarySide:
        """Which side of the boundary a position lies on.

        Uses the sign of the 2-D cross product between the nearest boundary segment's
        direction and the vector from that segment to the position. Traversing the
        boundary north-east to south-west, India lies to the right (negative cross
        product) and Sri Lanka to the left.
        """
        vessel = Point(lon, lat)
        coords = list(self._line.coords)

        best_index = 0
        best_distance = float("inf")
        for index in range(len(coords) - 1):
            segment = LineString([coords[index], coords[index + 1]])
            distance = segment.distance(vessel)
            if distance < best_distance:
                best_distance = distance
                best_index = index

        (x1, y1), (x2, y2) = coords[best_index], coords[best_index + 1]
        cross = (x2 - x1) * (lat - y1) - (y2 - y1) * (lon - x1)
        return BoundarySide.SRI_LANKA if cross > 0 else BoundarySide.INDIA

    def proximity(
        self, lat: float, lon: float, *, home_side: BoundarySide | None = None
    ) -> BoundaryProximity:
        """Full proximity assessment for one position.

        ``home_side`` is the side the vessel is licensed to fish; when the vessel is on
        the other side the band is ``CROSSED`` regardless of distance. Passing it is what
        turns "you are 300 m from the line" into "you are across the line".
        """
        nearest_lat, nearest_lon = self.nearest_point(lat, lon)
        distance = geodesic_distance_m(lat, lon, nearest_lat, nearest_lon)
        side = self.side_of(lat, lon)
        crossed = home_side is not None and side is not home_side
        return BoundaryProximity(
            lat=lat,
            lon=lon,
            distance_metres=distance,
            side=side,
            band=self._thresholds.band_for(distance, crossed=crossed),
            nearest_lat=nearest_lat,
            nearest_lon=nearest_lon,
            bearing_to_boundary_deg=geodesic_bearing_deg(lat, lon, nearest_lat, nearest_lon),
        )
