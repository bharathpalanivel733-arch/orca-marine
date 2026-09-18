"""Predictive drift and solver constraints (PLAN.md Phase 2.4-2.5)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from orca_geo import (
    BoundarySide,
    ConstraintKind,
    ConstraintSet,
    ConstraintSeverity,
    DriftPredictor,
    ImblGeofence,
    ProtectedArea,
    SeasonalBan,
    SeasonalWindow,
    VesselMotion,
    WarningBand,
)
from orca_geo.drift import KNOTS_TO_MS, advance

# An Indian boat in Palk Bay, a few km west of the line near Kachchatheevu.
INDIAN_BOAT_LAT = 9.3900
INDIAN_BOAT_LON = 79.4500


@pytest.fixture(scope="module")
def predictor() -> DriftPredictor:
    return DriftPredictor(ImblGeofence())


class TestVesselMotion:
    def test_knots_convert_to_metres_per_second(self) -> None:
        motion = VesselMotion.from_knots(heading_deg=90, speed_knots=5)
        assert motion.speed_ms == pytest.approx(5 * KNOTS_TO_MS)

    def test_course_equals_heading_without_current(self) -> None:
        motion = VesselMotion.from_knots(heading_deg=90, speed_knots=5)
        assert motion.effective_course_deg == pytest.approx(90.0)

    def test_current_sets_the_vessel_off_its_heading(self) -> None:
        """The gap between heading and course over ground is the drift a skipper cannot see."""
        motion = VesselMotion.from_knots(heading_deg=0, speed_knots=3, current_east_ms=1.0)
        assert motion.effective_course_deg > 5.0
        assert motion.effective_speed_ms > motion.speed_ms

    def test_engines_off_means_pure_current_drift(self) -> None:
        """Nets out, engine off — the situation in which crossings actually happen."""
        motion = VesselMotion(heading_deg=0.0, speed_ms=0.0, current_east_ms=0.8)
        assert motion.effective_speed_ms == pytest.approx(0.8)
        assert motion.effective_course_deg == pytest.approx(90.0)

    def test_invalid_inputs_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="heading"):
            VesselMotion(heading_deg=370.0, speed_ms=1.0)
        with pytest.raises(ValueError, match="speed"):
            VesselMotion(heading_deg=0.0, speed_ms=-1.0)


class TestGeodesicAdvance:
    def test_moving_east_increases_longitude(self) -> None:
        lat, lon = advance(9.0, 79.0, course_deg=90.0, distance_m=10_000)
        assert lon > 79.0
        assert lat == pytest.approx(9.0, abs=1e-3)

    def test_moving_north_increases_latitude(self) -> None:
        lat, lon = advance(9.0, 79.0, course_deg=0.0, distance_m=10_000)
        assert lat > 9.0
        assert lon == pytest.approx(79.0, abs=1e-6)


class TestPredictiveDrift:
    def test_a_boat_heading_at_the_boundary_is_warned_before_it_crosses(
        self, predictor: DriftPredictor
    ) -> None:
        """The Tier-1 differentiator: warn ahead of the crossing, not after it."""
        motion = VesselMotion.from_knots(heading_deg=90, speed_knots=6)

        forecast = predictor.project(
            INDIAN_BOAT_LAT, INDIAN_BOAT_LON, motion, horizon=timedelta(hours=1)
        )

        assert forecast.will_cross
        assert forecast.time_to_boundary is not None
        assert timedelta(0) < forecast.time_to_boundary < timedelta(hours=1)
        assert forecast.crossing_lat is not None
        assert forecast.track[0].side is BoundarySide.INDIA

    def test_a_boat_heading_away_is_not_warned(self, predictor: DriftPredictor) -> None:
        motion = VesselMotion.from_knots(heading_deg=270, speed_knots=6)

        forecast = predictor.project(
            INDIAN_BOAT_LAT, INDIAN_BOAT_LON, motion, horizon=timedelta(hours=1)
        )

        assert not forecast.will_cross
        assert forecast.time_to_boundary is None

    def test_current_alone_can_carry_a_stationary_boat_across(
        self, predictor: DriftPredictor
    ) -> None:
        """Engine off, drifting east on the current — nobody is steering anywhere."""
        motion = VesselMotion(heading_deg=0.0, speed_ms=0.0, current_east_ms=1.2)

        forecast = predictor.project(
            INDIAN_BOAT_LAT, INDIAN_BOAT_LON, motion, horizon=timedelta(hours=2)
        )

        assert forecast.will_cross, "a drifting boat must still be warned"

    def test_time_to_boundary_is_independent_of_step_size(self, predictor: DriftPredictor) -> None:
        """A coarse and a fine sampling must agree, or the number means nothing."""
        motion = VesselMotion.from_knots(heading_deg=90, speed_knots=6)

        coarse = predictor.project(
            INDIAN_BOAT_LAT,
            INDIAN_BOAT_LON,
            motion,
            horizon=timedelta(hours=1),
            step=timedelta(minutes=5),
        )
        fine = predictor.project(
            INDIAN_BOAT_LAT,
            INDIAN_BOAT_LON,
            motion,
            horizon=timedelta(hours=1),
            step=timedelta(seconds=20),
        )

        assert coarse.time_to_boundary is not None
        assert fine.time_to_boundary is not None
        difference = abs(
            coarse.time_to_boundary.total_seconds() - fine.time_to_boundary.total_seconds()
        )
        assert difference < 15.0

    def test_faster_boat_reaches_the_boundary_sooner(self, predictor: DriftPredictor) -> None:
        slow = predictor.project(
            INDIAN_BOAT_LAT,
            INDIAN_BOAT_LON,
            VesselMotion.from_knots(heading_deg=90, speed_knots=3),
            horizon=timedelta(hours=3),
        )
        fast = predictor.project(
            INDIAN_BOAT_LAT,
            INDIAN_BOAT_LON,
            VesselMotion.from_knots(heading_deg=90, speed_knots=9),
            horizon=timedelta(hours=3),
        )

        assert slow.time_to_boundary is not None
        assert fast.time_to_boundary is not None
        assert fast.time_to_boundary < slow.time_to_boundary

    def test_track_bands_escalate_as_the_boundary_nears(self, predictor: DriftPredictor) -> None:
        motion = VesselMotion.from_knots(heading_deg=90, speed_knots=6)

        forecast = predictor.project(
            INDIAN_BOAT_LAT, INDIAN_BOAT_LON, motion, horizon=timedelta(hours=1)
        )

        bands = [position.band for position in forecast.track]
        assert WarningBand.CROSSED in bands
        assert forecast.closest_approach.distance_metres <= forecast.track[0].distance_metres

    def test_assumptions_are_reported_with_the_forecast(self, predictor: DriftPredictor) -> None:
        """A safety prediction must carry its own limitations."""
        forecast = predictor.project(
            INDIAN_BOAT_LAT,
            INDIAN_BOAT_LON,
            VesselMotion.from_knots(heading_deg=90, speed_knots=6),
        )
        joined = " ".join(forecast.assumptions)
        assert "current sampled once" in joined
        assert "wind leeway" in joined

    def test_invalid_horizon_or_step_is_rejected(self, predictor: DriftPredictor) -> None:
        motion = VesselMotion.from_knots(heading_deg=90, speed_knots=6)
        with pytest.raises(ValueError, match="step"):
            predictor.project(INDIAN_BOAT_LAT, INDIAN_BOAT_LON, motion, step=timedelta(0))
        with pytest.raises(ValueError, match="horizon"):
            predictor.project(INDIAN_BOAT_LAT, INDIAN_BOAT_LON, motion, horizon=timedelta(0))


class TestSeasonalWindows:
    def test_window_inside_one_year(self) -> None:
        window = SeasonalWindow(start_month=4, start_day=15, end_month=6, end_day=14)
        assert window.contains(date(2026, 5, 1))
        assert not window.contains(date(2026, 7, 1))

    def test_window_wrapping_the_year_end(self) -> None:
        """A December-February closure must not silently evaluate to nothing."""
        window = SeasonalWindow(start_month=12, start_day=1, end_month=2, end_day=15)
        assert window.contains(date(2026, 12, 20))
        assert window.contains(date(2026, 1, 10))
        assert not window.contains(date(2026, 6, 1))

    def test_boundary_days_are_inclusive(self) -> None:
        window = SeasonalWindow(start_month=4, start_day=15, end_month=6, end_day=14)
        assert window.contains(date(2026, 4, 15))
        assert window.contains(date(2026, 6, 14))
        assert not window.contains(date(2026, 6, 15))

    def test_invalid_dates_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="month"):
            SeasonalWindow(start_month=13, start_day=1, end_month=2, end_day=1)


class TestConstraints:
    def _mpa(self, **overrides) -> ProtectedArea:
        defaults = {
            "area_id": "mpa_gulf_of_mannar",
            "name": "Gulf of Mannar Marine National Park",
            "kind": ConstraintKind.MARINE_PROTECTED_AREA,
            "severity": ConstraintSeverity.HARD,
            "authority": "Tamil Nadu Forest Department",
            "geometry_wkt": "POLYGON((79.0 8.9, 79.3 8.9, 79.3 9.2, 79.0 9.2, 79.0 8.9))",
            "citation": "test fixture geometry, not authoritative",
        }
        return ProtectedArea(**(defaults | overrides))

    def test_a_no_take_area_is_closed_year_round(self) -> None:
        """No seasonal windows means always closed, never never-closed."""
        area = self._mpa()
        assert area.is_closed_on(date(2026, 1, 1))
        assert area.is_closed_on(date(2026, 8, 1))

    def test_a_seasonally_closed_area_opens_outside_its_window(self) -> None:
        area = self._mpa(
            closed_seasons=(SeasonalWindow(start_month=4, start_day=15, end_month=6, end_day=14),)
        )
        assert area.is_closed_on(date(2026, 5, 1))
        assert not area.is_closed_on(date(2026, 9, 1))

    def test_containment_violation_is_reported_with_its_authority(self) -> None:
        constraints = ConstraintSet(areas=(self._mpa(),))

        violations = constraints.evaluate_areas(
            frozenset({"mpa_gulf_of_mannar"}), date(2026, 9, 18)
        )

        assert len(violations) == 1
        assert violations[0].blocks_plan
        assert violations[0].authority == "Tamil Nadu Forest Department"

    def test_areas_not_containing_the_position_do_not_violate(self) -> None:
        constraints = ConstraintSet(areas=(self._mpa(),))
        assert constraints.evaluate_areas(frozenset(), date(2026, 9, 18)) == ()

    def test_seasonal_ban_applies_only_in_its_jurisdiction(self) -> None:
        ban = SeasonalBan(
            ban_id="tn_monsoon_ban",
            name="Tamil Nadu east-coast fishing ban",
            jurisdiction="IN-TN",
            window=SeasonalWindow(start_month=4, start_day=15, end_month=6, end_day=14),
            authority="Government of Tamil Nadu (dates are reference data, load before use)",
        )
        constraints = ConstraintSet(bans=(ban,))
        during = date(2026, 5, 1)

        assert constraints.evaluate_temporal(during, frozenset({"IN-TN"}))
        assert not constraints.evaluate_temporal(during, frozenset({"IN-KL"}))
        assert not constraints.evaluate_temporal(date(2026, 9, 1), frozenset({"IN-TN"}))

    def test_hard_and_soft_violations_are_separated(self) -> None:
        hard = self._mpa()
        soft = self._mpa(
            area_id="advisory_zone",
            name="Voluntary conservation zone",
            severity=ConstraintSeverity.SOFT,
        )
        constraints = ConstraintSet(areas=(hard, soft))

        violations = constraints.evaluate_areas(
            frozenset({"mpa_gulf_of_mannar", "advisory_zone"}), date(2026, 9, 18)
        )
        blocking = ConstraintSet.blocking(violations)

        assert len(violations) == 2
        assert len(blocking) == 1
        assert blocking[0].constraint_id == "mpa_gulf_of_mannar"
