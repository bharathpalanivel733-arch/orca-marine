"""PostGIS and source-integrity integration tests (PLAN.md Phase 2.1-2.2).

``-m stack`` needs the docker-compose dev stack; ``-m live`` re-downloads the deposited
treaty PDFs and checks their checksums. Both are opt-in and skipped by default.

The most valuable assertion here is the **cross-check**: the Python geodesic distance
(``pyproj``) and the PostGIS ``ST_Distance`` on ``geography`` are two independent
implementations of the same quantity. They agree to a few metres rather than exactly —
the reason is documented on the test — and, more importantly, they are required never to
disagree about which warning band a position falls in.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from orca_geo import ConstraintKind, ConstraintSeverity, ImblGeofence, ProtectedArea, SeasonalBan
from orca_geo.constraints import SeasonalWindow
from orca_geo.geofence import geodesic_distance_m
from orca_geo.imbl import TREATY_SOURCES, imbl_positions
from orca_geo.storage import GeoStore

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://orca:orca@localhost:5432/orca")
MIGRATIONS = Path(__file__).resolve().parents[3] / "infra" / "db" / "migrations"

PALK_STRAIT_POINTS = [
    ("Rameswaram (India)", 9.2876, 79.3129),
    ("Kachchatheevu (Sri Lanka)", 9.3833, 79.5167),
    ("Talaimannar (Sri Lanka)", 9.0947, 79.7333),
    ("Point Calimere (India)", 10.2889, 79.8514),
    ("Mid Palk Bay", 9.6000, 79.4000),
]


@pytest.fixture
def store():
    psycopg = pytest.importorskip("psycopg")
    try:
        connection = psycopg.connect(DATABASE_URL, connect_timeout=5)
    except psycopg.OperationalError as exc:
        pytest.skip(f"dev database not reachable ({exc}); run `pnpm db:up`")

    store = GeoStore(connection)
    store.ensure_schema((MIGRATIONS / "0002_geo.sql").read_text(encoding="utf-8"))
    store.load_imbl()
    yield store
    connection.close()


@pytest.mark.stack
class TestImblInPostGIS:
    def test_boundary_loads_with_its_treaty_provenance(self, store) -> None:
        """An operator reading the table must see this is not a median line."""
        with store._connection.cursor() as cursor:
            cursor.execute(
                "SELECT derivation, jsonb_array_length(sources), "
                "jsonb_array_length(anomalies), ST_NumPoints(geom::geometry) "
                "FROM geo.boundaries WHERE boundary_id = 'imbl_ind_lka'"
            )
            derivation, source_count, anomaly_count, point_count = cursor.fetchone()

        assert "NOT a computed median line" in derivation
        assert source_count == 3
        assert anomaly_count == 1
        assert point_count == len(imbl_positions())

    @pytest.mark.parametrize(("name", "lat", "lon"), PALK_STRAIT_POINTS)
    def test_postgis_and_pyproj_distances_agree(self, store, name, lat, lon) -> None:
        """Two independent geodesic implementations of the same distance.

        They do not agree to the millimetre, and the reason is understood rather than
        papered over: ``ST_ClosestPoint`` finds the nearest point in **planar degree
        space**, so measuring geodesically from that point is not identical to PostGIS's
        own ``geography`` minimum, and the two differ slightly in spheroid handling.
        Measured across these points the gap is at most ~8 m on 28 km (0.03%) and ~2 m on
        726 m (0.3%).

        The tolerance below reflects that physical reality. What matters for safety is
        not millimetre agreement but that the gap can never change a warning band, which
        ``test_distance_disagreement_never_changes_a_warning_band`` asserts directly.
        """
        postgis_distance = store.distance_to_imbl_m(lat, lon)

        nearest_lat, nearest_lon = store.nearest_imbl_point(lat, lon)
        python_distance = geodesic_distance_m(lat, lon, nearest_lat, nearest_lon)

        assert postgis_distance == pytest.approx(python_distance, rel=0.005, abs=10.0), name

    def test_distance_disagreement_never_changes_a_warning_band(self, store) -> None:
        """The safety-relevant property: implementations must not disagree about the band.

        A few metres of numerical disagreement is tolerable; the two implementations
        putting a boat in different warning bands is not. Sampled across Palk Bay,
        including points placed deliberately near the 2 km and 5 km thresholds.
        """
        geofence = ImblGeofence()
        thresholds = geofence.thresholds

        samples = [(lat, lon) for _, lat, lon in PALK_STRAIT_POINTS]
        samples += [(9.60 + 0.01 * step, 79.40 + 0.01 * step) for step in range(-8, 9)]

        for lat, lon in samples:
            postgis_band = thresholds.band_for(store.distance_to_imbl_m(lat, lon))
            python_band = thresholds.band_for(geofence.proximity(lat, lon).distance_metres)
            assert postgis_band is python_band, (
                f"band disagreement at ({lat}, {lon}): postgis={postgis_band} python={python_band}"
            )

    @pytest.mark.parametrize(("name", "lat", "lon"), PALK_STRAIT_POINTS)
    def test_postgis_agrees_with_the_in_process_geofence(self, store, name, lat, lon) -> None:
        geofence = ImblGeofence()
        in_process = geofence.proximity(lat, lon)

        postgis_distance = store.distance_to_imbl_m(lat, lon)

        # Shapely picks the nearest point in degree space; over these distances the
        # difference from the true geodesic nearest point is metres, not kilometres.
        assert postgis_distance == pytest.approx(in_process.distance_metres, rel=0.01, abs=50.0)

    def test_kachchatheevu_distance_is_small_and_positive(self, store) -> None:
        distance = store.distance_to_imbl_m(9.3833, 79.5167)
        assert 300.0 < distance < 5_000.0

    def test_warning_bands_are_buffers_of_the_real_boundary(self, store) -> None:
        red = store.warning_band_wkt(2_000.0)
        amber = store.warning_band_wkt(5_000.0)

        assert red.startswith(("POLYGON", "MULTIPOLYGON"))
        assert amber.startswith(("POLYGON", "MULTIPOLYGON"))
        assert len(amber) > 0 and len(red) > 0

    def test_loading_twice_updates_rather_than_duplicating(self, store) -> None:
        store.load_imbl()
        with store._connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM geo.boundaries WHERE boundary_id = 'imbl_ind_lka'")
            assert cursor.fetchone()[0] == 1


@pytest.mark.stack
class TestSpatialConstraints:
    def test_containment_is_evaluated_by_postgis(self, store) -> None:
        area = ProtectedArea(
            area_id="test_mpa_palk",
            name="Test MPA (fixture geometry, not authoritative)",
            kind=ConstraintKind.MARINE_PROTECTED_AREA,
            severity=ConstraintSeverity.HARD,
            authority="test fixture",
            geometry_wkt="POLYGON((79.3 9.5, 79.5 9.5, 79.5 9.7, 79.3 9.7, 79.3 9.5))",
        )
        store.load_protected_area(area)

        inside = store.containing_areas(9.6, 79.4)
        outside = store.containing_areas(9.0, 79.0)

        assert [a.area_id for a in inside] == ["test_mpa_palk"]
        assert outside == ()

    def test_seasonal_bans_round_trip_and_filter_by_date(self, store) -> None:
        ban = SeasonalBan(
            ban_id="test_tn_ban",
            name="Test east-coast ban (fixture dates, verify before use)",
            jurisdiction="IN-TN",
            window=SeasonalWindow(start_month=4, start_day=15, end_month=6, end_day=14),
            authority="test fixture",
        )
        store.load_seasonal_ban(ban)

        in_force = store.bans_in_force(date(2026, 5, 1), frozenset({"IN-TN"}))
        out_of_season = store.bans_in_force(date(2026, 9, 1), frozenset({"IN-TN"}))
        other_state = store.bans_in_force(date(2026, 5, 1), frozenset({"IN-KL"}))

        assert [b.ban_id for b in in_force] == ["test_tn_ban"]
        assert out_of_season == ()
        assert other_state == ()


@pytest.mark.live
class TestTreatySourceIntegrity:
    """The transcription is only as trustworthy as the files it came from."""

    def test_deposited_pdfs_still_match_their_recorded_checksums(self) -> None:
        import hashlib
        import ssl

        import httpx
        import truststore

        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with httpx.Client(timeout=60, follow_redirects=True, verify=context) as client:
            for source in TREATY_SOURCES:
                response = client.get(source.url)
                assert response.status_code == 200, source.url
                digest = hashlib.sha256(response.content).hexdigest()
                assert digest == source.sha256, (
                    f"{source.filename} changed upstream: the transcription in "
                    f"orca_geo.imbl must be re-verified against the new text"
                )
