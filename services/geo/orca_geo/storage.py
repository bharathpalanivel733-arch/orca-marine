"""PostGIS loading and authoritative spatial queries (PLAN.md Phase 2.1-2.2, 2.5).

PostGIS is the authority for metric distance and containment. ``ST_Distance`` on the
``geography`` type measures on the spheroid, which is what a warning band must be based
on — planar distance in degrees would be wrong by a latitude-dependent factor and would
under-warn near the boundary.

The Python geodesic implementation in ``geofence.py`` exists for in-process work without
a database round trip. The two are cross-checked in the stack tests: a few metres of
numerical disagreement is expected and tolerated, but the two putting a vessel in
different warning bands is a build failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from orca_geo.constraints import ProtectedArea, SeasonalBan, SeasonalWindow
from orca_geo.imbl import imbl_linestring_wkt, imbl_provenance

IMBL_BOUNDARY_ID = "imbl_ind_lka"

UPSERT_BOUNDARY_SQL = """
INSERT INTO geo.boundaries (boundary_id, name, parties, derivation, sources, anomalies, geom)
VALUES (
    %(boundary_id)s, %(name)s, %(parties)s, %(derivation)s,
    %(sources)s::jsonb, %(anomalies)s::jsonb,
    ST_GeogFromText(%(wkt)s)
)
ON CONFLICT (boundary_id) DO UPDATE SET
    name = EXCLUDED.name,
    parties = EXCLUDED.parties,
    derivation = EXCLUDED.derivation,
    sources = EXCLUDED.sources,
    anomalies = EXCLUDED.anomalies,
    geom = EXCLUDED.geom,
    loaded_at = now()
"""

DISTANCE_SQL = """
SELECT ST_Distance(geom, ST_GeogFromText(%(point)s)) AS distance_m
FROM geo.boundaries
WHERE boundary_id = %(boundary_id)s
"""

NEAREST_POINT_SQL = """
SELECT ST_Y(closest::geometry) AS lat, ST_X(closest::geometry) AS lon
FROM (
    SELECT ST_ClosestPoint(geom::geometry, ST_GeomFromText(%(point)s, 4326))::geography AS closest
    FROM geo.boundaries
    WHERE boundary_id = %(boundary_id)s
) AS nearest
"""

CONTAINING_AREAS_SQL = """
SELECT area_id, name, kind, severity, authority, citation
FROM geo.protected_areas
WHERE ST_Intersects(geom, ST_GeogFromText(%(point)s))
"""

BANDS_SQL = """
SELECT ST_AsText(ST_Buffer(geom, %(metres)s)::geometry) AS band
FROM geo.boundaries
WHERE boundary_id = %(boundary_id)s
"""


@dataclass(frozen=True)
class ContainingArea:
    """A protected area or restricted zone that contains a queried position."""

    area_id: str
    name: str
    kind: str
    severity: str
    authority: str
    citation: str | None


def point_wkt(lat: float, lon: float) -> str:
    """WKT POINT in lon-lat order, which is what PostGIS expects."""
    return f"SRID=4326;POINT({lon:.6f} {lat:.6f})"


class GeoStore:
    """Loads reference geometry and answers spatial questions from PostGIS."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def ensure_schema(self, migration_sql: str) -> None:
        """Apply a migration. Idempotent."""
        with self._connection.cursor() as cursor:
            cursor.execute(migration_sql)
        self._connection.commit()

    def load_imbl(self) -> None:
        """Load the treaty-transcribed IMBL.

        The provenance travels into the database with the geometry, so an operator
        inspecting the table can see it came from the 1974/1976 agreements and not from a
        median-line computation.
        """
        provenance = imbl_provenance()
        with self._connection.cursor() as cursor:
            cursor.execute(
                UPSERT_BOUNDARY_SQL,
                {
                    "boundary_id": IMBL_BOUNDARY_ID,
                    "name": str(provenance["name"]),
                    "parties": ["IND", "LKA"],
                    "derivation": str(provenance["derivation"]),
                    "sources": json.dumps(provenance["sources"]),
                    "anomalies": json.dumps(provenance["anomalies"]),
                    "wkt": f"SRID=4326;{imbl_linestring_wkt()}",
                },
            )
        self._connection.commit()

    def distance_to_imbl_m(self, lat: float, lon: float) -> float:
        """Authoritative geodesic distance to the boundary, in metres."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                DISTANCE_SQL, {"point": point_wkt(lat, lon), "boundary_id": IMBL_BOUNDARY_ID}
            )
            row = cursor.fetchone()
        if row is None:
            msg = f"boundary {IMBL_BOUNDARY_ID} is not loaded; call load_imbl() first"
            raise LookupError(msg)
        return float(row[0])

    def nearest_imbl_point(self, lat: float, lon: float) -> tuple[float, float]:
        """Closest point on the boundary, as (lat, lon)."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                NEAREST_POINT_SQL,
                {"point": f"POINT({lon:.6f} {lat:.6f})", "boundary_id": IMBL_BOUNDARY_ID},
            )
            row = cursor.fetchone()
        if row is None:
            msg = f"boundary {IMBL_BOUNDARY_ID} is not loaded; call load_imbl() first"
            raise LookupError(msg)
        return (float(row[0]), float(row[1]))

    def warning_band_wkt(self, metres: float) -> str:
        """The buffered warning band around the boundary, as WKT.

        Used to render amber and red bands on the map, so the UI draws the same geometry
        the warnings are computed from rather than an approximation of it.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(BANDS_SQL, {"metres": metres, "boundary_id": IMBL_BOUNDARY_ID})
            row = cursor.fetchone()
        if row is None:
            msg = f"boundary {IMBL_BOUNDARY_ID} is not loaded; call load_imbl() first"
            raise LookupError(msg)
        return str(row[0])

    def containing_areas(self, lat: float, lon: float) -> tuple[ContainingArea, ...]:
        """Protected areas and restricted zones containing a position."""
        with self._connection.cursor() as cursor:
            cursor.execute(CONTAINING_AREAS_SQL, {"point": point_wkt(lat, lon)})
            rows = cursor.fetchall()
        return tuple(
            ContainingArea(
                area_id=row[0],
                name=row[1],
                kind=row[2],
                severity=row[3],
                authority=row[4],
                citation=row[5],
            )
            for row in rows
        )

    def load_protected_area(self, area: ProtectedArea) -> None:
        """Insert or update one protected area from its authoritative geometry."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO geo.protected_areas
                    (area_id, name, kind, severity, authority, citation, geom)
                VALUES (%(area_id)s, %(name)s, %(kind)s, %(severity)s, %(authority)s,
                        %(citation)s, ST_Multi(ST_GeogFromText(%(wkt)s)::geometry)::geography)
                ON CONFLICT (area_id) DO UPDATE SET
                    name = EXCLUDED.name, kind = EXCLUDED.kind,
                    severity = EXCLUDED.severity, authority = EXCLUDED.authority,
                    citation = EXCLUDED.citation, geom = EXCLUDED.geom, loaded_at = now()
                """,
                {
                    "area_id": area.area_id,
                    "name": area.name,
                    "kind": area.kind.value,
                    "severity": area.severity.value,
                    "authority": area.authority,
                    "citation": area.citation,
                    "wkt": f"SRID=4326;{area.geometry_wkt}",
                },
            )
        self._connection.commit()

    def load_seasonal_ban(self, ban: SeasonalBan) -> None:
        """Insert or update one seasonal ban."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO geo.seasonal_bans
                    (ban_id, name, jurisdiction, authority, citation, severity,
                     start_month, start_day, end_month, end_day)
                VALUES (%(ban_id)s, %(name)s, %(jurisdiction)s, %(authority)s, %(citation)s,
                        %(severity)s, %(start_month)s, %(start_day)s, %(end_month)s, %(end_day)s)
                ON CONFLICT (ban_id) DO UPDATE SET
                    name = EXCLUDED.name, jurisdiction = EXCLUDED.jurisdiction,
                    authority = EXCLUDED.authority, citation = EXCLUDED.citation,
                    severity = EXCLUDED.severity,
                    start_month = EXCLUDED.start_month, start_day = EXCLUDED.start_day,
                    end_month = EXCLUDED.end_month, end_day = EXCLUDED.end_day,
                    loaded_at = now()
                """,
                {
                    "ban_id": ban.ban_id,
                    "name": ban.name,
                    "jurisdiction": ban.jurisdiction,
                    "authority": ban.authority,
                    "citation": ban.citation,
                    "severity": ban.severity.value,
                    "start_month": ban.window.start_month,
                    "start_day": ban.window.start_day,
                    "end_month": ban.window.end_month,
                    "end_day": ban.window.end_day,
                },
            )
        self._connection.commit()

    def seasonal_bans(self) -> tuple[SeasonalBan, ...]:
        """All loaded seasonal bans."""
        from orca_geo.constraints import ConstraintSeverity

        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT ban_id, name, jurisdiction, authority, citation, severity, "
                "start_month, start_day, end_month, end_day FROM geo.seasonal_bans"
            )
            rows = cursor.fetchall()
        return tuple(
            SeasonalBan(
                ban_id=row[0],
                name=row[1],
                jurisdiction=row[2],
                authority=row[3],
                citation=row[4],
                severity=ConstraintSeverity(row[5]),
                window=SeasonalWindow(
                    start_month=row[6], start_day=row[7], end_month=row[8], end_day=row[9]
                ),
            )
            for row in rows
        )

    def bans_in_force(self, moment: date, jurisdictions: frozenset[str]) -> tuple[SeasonalBan, ...]:
        """Seasonal bans in force on a date for the given jurisdictions."""
        return tuple(
            ban
            for ban in self.seasonal_bans()
            if ban.jurisdiction in jurisdictions and ban.window.contains(moment)
        )
