"""IMBL transcription and geofence regression tests (PLAN.md Phase 2.6).

The Palk Strait points below are **independently known geography**, not values produced by
this code. Kachchatheevu was placed on the Sri Lankan side by the 1974 agreement;
Rameswaram, Point Calimere and Vedaranyam are Indian territory; Talaimannar, Mannar and
Kankesanthurai are Sri Lankan. If a refactor ever flips the sign of the side test or
corrupts a transcribed coordinate, these assertions fail before anyone is mis-warned.
"""

from __future__ import annotations

import pytest

from orca_geo import BandThresholds, BoundarySide, ImblGeofence, WarningBand
from orca_geo.imbl import (
    BAY_OF_BENGAL_1976,
    GULF_OF_MANNAR_1976,
    PALK_STRAIT_1974,
    SOURCE_ANOMALIES,
    TREATY_SOURCES,
    dm,
    imbl_linestring_wkt,
    imbl_positions,
    imbl_provenance,
)

# (name, lat, lon, expected side) — all independently known, none derived from this code.
INDIAN_SIDE = [
    ("Rameswaram", 9.2876, 79.3129),
    ("Point Calimere", 10.2889, 79.8514),
    ("Vedaranyam coast", 10.3750, 79.8500),
    ("Tondi, Palk Bay", 9.7400, 79.0170),
]

SRI_LANKAN_SIDE = [
    ("Kachchatheevu", 9.3833, 79.5167),
    ("Talaimannar", 9.0947, 79.7333),
    ("Kankesanthurai (Jaffna)", 9.8153, 80.0417),
    ("Mannar town", 8.9810, 79.9040),
]


@pytest.fixture(scope="module")
def geofence() -> ImblGeofence:
    return ImblGeofence()


class TestTreatyTranscription:
    """The geometry must remain what the treaties say."""

    def test_degrees_minutes_conversion(self) -> None:
        assert dm(9, 30.0) == pytest.approx(9.5)
        assert dm(9, 40.15) == pytest.approx(9.669166, abs=1e-6)

    def test_minutes_must_be_valid(self) -> None:
        with pytest.raises(ValueError, match="minutes must be"):
            dm(9, 60.0)

    def test_1974_agreement_has_its_six_positions(self) -> None:
        assert len(PALK_STRAIT_1974) == 6
        first = PALK_STRAIT_1974[0]
        assert first.lat == pytest.approx(dm(10, 5.0))
        assert first.lon == pytest.approx(dm(80, 3.0))

    def test_1976_gulf_of_mannar_has_its_thirteen_positions(self) -> None:
        assert len(GULF_OF_MANNAR_1976) == 13
        assert GULF_OF_MANNAR_1976[-1].label == "13m"

    def test_1976_bay_of_bengal_has_its_eight_positions(self) -> None:
        assert len(BAY_OF_BENGAL_1976) == 8

    def test_palk_strait_and_gulf_of_mannar_join_at_the_same_point(self) -> None:
        """1974 Position 6 and 1976 position 1m are the same point on the sea."""
        palk_last = PALK_STRAIT_1974[-1]
        mannar_first = GULF_OF_MANNAR_1976[0]
        assert palk_last.lat == pytest.approx(mannar_first.lat)
        assert palk_last.lon == pytest.approx(mannar_first.lon)

    def test_palk_strait_and_bay_of_bengal_join_at_the_same_point(self) -> None:
        """1974 Position 1 and 1976 position 1b are the same point on the sea."""
        palk_first = PALK_STRAIT_1974[0]
        bay_first = BAY_OF_BENGAL_1976[0]
        assert palk_first.lat == pytest.approx(bay_first.lat)
        assert palk_first.lon == pytest.approx(bay_first.lon)

    def test_shared_positions_are_not_duplicated_in_the_assembled_line(self) -> None:
        """A duplicated vertex would insert a zero-length segment."""
        positions = imbl_positions()
        seen = {(round(p.lat, 6), round(p.lon, 6)) for p in positions}
        assert len(seen) == len(positions)

    def test_assembled_boundary_runs_north_east_to_south_west(self) -> None:
        positions = imbl_positions()
        assert positions[0].label == "6b"
        assert positions[-1].label == "T"
        assert positions[0].lat > positions[-1].lat

    def test_every_position_lies_in_the_palk_strait_region(self) -> None:
        """A transcription slip would most likely land a point outside the region."""
        for position in imbl_positions():
            assert 4.0 <= position.lat <= 12.0, position.label
            assert 76.0 <= position.lon <= 84.0, position.label

    def test_4m_longitude_anomaly_is_documented_and_corrected(self) -> None:
        """The deposited text writes 4 m's longitude with an 'N' hemisphere."""
        position_4m = next(p for p in GULF_OF_MANNAR_1976 if p.label == "4m")
        assert position_4m.lon == pytest.approx(dm(79, 18.2))
        assert any("4 m" in anomaly for anomaly in SOURCE_ANOMALIES)

    def test_provenance_names_the_treaties_and_denies_a_median_line(self) -> None:
        provenance = imbl_provenance()
        assert "NOT a computed median line" in str(provenance["derivation"])
        assert len(provenance["sources"]) == 3  # type: ignore[arg-type]
        assert all(len(source.sha256) == 64 for source in TREATY_SOURCES)

    def test_linestring_wkt_is_well_formed(self) -> None:
        wkt = imbl_linestring_wkt()
        assert wkt.startswith("LINESTRING(")
        assert wkt.count(",") == len(imbl_positions()) - 1


class TestPalkStraitRegression:
    """Known points on each side of the boundary."""

    @pytest.mark.parametrize(("name", "lat", "lon"), INDIAN_SIDE)
    def test_indian_points_are_on_the_indian_side(
        self, geofence: ImblGeofence, name: str, lat: float, lon: float
    ) -> None:
        assert geofence.side_of(lat, lon) is BoundarySide.INDIA, name

    @pytest.mark.parametrize(("name", "lat", "lon"), SRI_LANKAN_SIDE)
    def test_sri_lankan_points_are_on_the_sri_lankan_side(
        self, geofence: ImblGeofence, name: str, lat: float, lon: float
    ) -> None:
        assert geofence.side_of(lat, lon) is BoundarySide.SRI_LANKA, name

    def test_kachchatheevu_sits_just_across_the_line(self, geofence: ImblGeofence) -> None:
        """The 1974 line was drawn immediately west of the island, ceding it to Sri Lanka.

        An independent corroboration of the transcription: if a coordinate were wrong,
        the island would not land a short distance on the Sri Lankan side.
        """
        proximity = geofence.proximity(9.3833, 79.5167)
        assert proximity.side is BoundarySide.SRI_LANKA
        assert 0.3 < proximity.distance_km < 5.0

    def test_an_indian_boat_near_kachchatheevu_gets_a_red_warning(
        self, geofence: ImblGeofence
    ) -> None:
        """The scenario behind the 2025 arrests: fishing right up to the line."""
        proximity = geofence.proximity(9.3900, 79.4800, home_side=BoundarySide.INDIA)
        assert proximity.band in {WarningBand.RED, WarningBand.AMBER, WarningBand.CROSSED}
        assert proximity.distance_km < 5.0

    def test_crossing_is_reported_even_when_distance_is_small(self, geofence: ImblGeofence) -> None:
        """Being 300 m past the line is a crossing, not a near miss."""
        proximity = geofence.proximity(9.3833, 79.5167, home_side=BoundarySide.INDIA)
        assert proximity.band is WarningBand.CROSSED

    def test_distance_is_symmetric_about_the_line(self, geofence: ImblGeofence) -> None:
        indian = geofence.proximity(9.2876, 79.3129)
        lankan = geofence.proximity(9.0947, 79.7333)
        assert indian.side is not lankan.side
        assert indian.distance_metres > 0
        assert lankan.distance_metres > 0


class TestWarningBands:
    def test_default_bands_match_the_documented_values(self) -> None:
        thresholds = BandThresholds()
        assert thresholds.amber_metres == 5_000.0
        assert thresholds.red_metres == 2_000.0

    @pytest.mark.parametrize(
        ("distance", "expected"),
        [
            (500.0, WarningBand.RED),
            (2_000.0, WarningBand.RED),
            (2_000.1, WarningBand.AMBER),
            (5_000.0, WarningBand.AMBER),
            (5_000.1, WarningBand.CLEAR),
            (50_000.0, WarningBand.CLEAR),
        ],
    )
    def test_band_boundaries_are_inclusive_at_the_threshold(
        self, distance: float, expected: WarningBand
    ) -> None:
        assert BandThresholds().band_for(distance) is expected

    def test_bands_are_configurable(self) -> None:
        cautious = BandThresholds(amber_metres=12_000.0, red_metres=6_000.0)
        assert cautious.band_for(8_000.0) is WarningBand.AMBER
        assert BandThresholds().band_for(8_000.0) is WarningBand.CLEAR

    def test_red_must_be_closer_than_amber(self) -> None:
        with pytest.raises(ValueError, match="must be closer than amber"):
            BandThresholds(amber_metres=2_000.0, red_metres=5_000.0)

    def test_crossing_overrides_any_distance(self) -> None:
        assert BandThresholds().band_for(40_000.0, crossed=True) is WarningBand.CROSSED


class TestNearestPoint:
    def test_nearest_point_lies_on_the_boundary(self, geofence: ImblGeofence) -> None:
        from shapely.geometry import LineString, Point

        lat, lon = geofence.nearest_point(9.2876, 79.3129)
        line = LineString([p.lonlat for p in imbl_positions()])
        assert line.distance(Point(lon, lat)) < 1e-9

    def test_bearing_points_towards_the_boundary(self, geofence: ImblGeofence) -> None:
        """From Rameswaram the boundary lies to the east."""
        proximity = geofence.proximity(9.2876, 79.3129)
        assert 45.0 < proximity.bearing_to_boundary_deg < 135.0
