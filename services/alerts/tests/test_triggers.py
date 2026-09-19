"""The deterministic alert rules (PLAN.md Phase 8.1).

Three families, one shared discipline: a rule that cannot see its driver returns
``UNEVALUATED``, never ``CLEAR``. Not seeing lightning data is not the same as seeing no
lightning, and a system that collapses the two is one that stays quiet through the storm it
was built for. That distinction is asserted for every rule that has an optional driver.

The vessel-relative family is tested against two boats deliberately: the same sea is a
routine afternoon for a trawler and a mortal risk for an open vallam, and a single
sea-state band for everyone would be the wrong abstraction.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from _alert_fixtures import NOW, PALK_BAY_LAT, PALK_BAY_LON, conditions, cyclone, picture
from orca_geo import (
    BoundaryProximity,
    BoundarySide,
    DriftForecast,
    ImblGeofence,
    VesselMotion,
    WarningBand,
)
from orca_geo.drift import DriftPredictor, ProjectedPosition
from orca_kernels import VesselProfile

from orca_alerts import (
    TriggerId,
    TriggerOutcome,
    check_cyclone_cone,
    check_cyclone_wind,
    check_geofence_proximity,
    check_lightning,
    check_predictive_drift,
    check_squall,
    check_wave_height,
    check_wind_speed,
    evaluate_cyclones,
    evaluate_vessel_conditions,
    worst,
)


class TestCycloneWindRadius:
    def test_a_vessel_in_the_eyewall_is_an_emergency(self) -> None:
        result = check_cyclone_wind(cyclone(), PALK_BAY_LAT, PALK_BAY_LON, now=NOW)

        assert result.outcome is TriggerOutcome.EMERGENCY
        assert result.observed["inside_ring_knots"] == 64

    def test_the_strongest_containing_ring_wins(self) -> None:
        """Rings nest; reporting the weakest would understate a vessel in the eyewall."""
        system = cyclone(rings=((27, 2.0), (34, 1.0)))

        # Inside the 27 kt ring but outside the 34 kt one.
        result = check_cyclone_wind(system, PALK_BAY_LAT + 1.5, PALK_BAY_LON, now=NOW)

        assert result.observed["inside_ring_knots"] == 27
        assert result.outcome is TriggerOutcome.WATCH

    def test_a_34_knot_containment_is_a_warning(self) -> None:
        system = cyclone(rings=((27, 2.0), (34, 1.0)))

        result = check_cyclone_wind(system, PALK_BAY_LAT + 0.5, PALK_BAY_LON, now=NOW)

        assert result.observed["inside_ring_knots"] == 34
        assert result.outcome is TriggerOutcome.WARNING

    def test_a_vessel_outside_every_ring_is_clear(self) -> None:
        result = check_cyclone_wind(cyclone(), PALK_BAY_LAT + 10, PALK_BAY_LON, now=NOW)

        assert result.outcome is TriggerOutcome.CLEAR
        assert result.observed["inside_ring_knots"] is None

    def test_the_result_attributes_the_publishing_authority(self) -> None:
        """ORCA relays IMD's warning; it does not author it."""
        result = check_cyclone_wind(cyclone(), PALK_BAY_LAT, PALK_BAY_LON, now=NOW)

        assert "imd" in result.threshold_source

    def test_containment_is_exact_not_a_bounding_box(self) -> None:
        """A box around a non-convex wind field would warn boats that are not in it."""
        from orca_alerts import GeoPolygon, WindRadius
        from orca_alerts.conditions import CycloneSystem

        # An L-shaped ring: the notch is inside the bounding box but outside the polygon.
        notched = GeoPolygon(
            ((9.0, 79.0), (9.0, 80.0), (9.5, 80.0), (9.5, 79.5), (10.0, 79.5), (10.0, 79.0))
        )
        system = CycloneSystem(
            system_id="s",
            name="Notch",
            centre_lat=9.5,
            centre_lon=79.25,
            issued_time=NOW,
            wind_radii=(WindRadius(knots=64, polygon=notched),),
        )

        in_notch = check_cyclone_wind(system, 9.8, 79.8, now=NOW)
        in_polygon = check_cyclone_wind(system, 9.2, 79.2, now=NOW)

        assert in_notch.outcome is TriggerOutcome.CLEAR
        assert in_polygon.outcome is TriggerOutcome.EMERGENCY


class TestCycloneCone:
    def test_being_in_the_cone_is_a_watch_not_a_warning(self) -> None:
        """The cone is where the storm *may* go. Warning on it would cry wolf every time."""
        result = check_cyclone_cone(cyclone(), PALK_BAY_LAT, PALK_BAY_LON, now=NOW)

        assert result.outcome is TriggerOutcome.WATCH

    def test_outside_the_cone_is_clear(self) -> None:
        result = check_cyclone_cone(cyclone(), PALK_BAY_LAT + 10, PALK_BAY_LON, now=NOW)

        assert result.outcome is TriggerOutcome.CLEAR

    def test_a_system_with_no_published_cone_is_unevaluated(self) -> None:
        """No cone is not the same as being outside one."""
        result = check_cyclone_cone(
            cyclone(cone_half_degrees=None), PALK_BAY_LAT, PALK_BAY_LON, now=NOW
        )

        assert result.outcome is TriggerOutcome.UNEVALUATED


class TestStaleHazards:
    def test_a_stale_cyclone_position_is_not_evaluated(self) -> None:
        """A nine-hour-old position describes where the storm was, not where it is."""
        stale = picture(cyclones=(cyclone(age=timedelta(hours=9)),))

        results = evaluate_cyclones(stale, PALK_BAY_LAT, PALK_BAY_LON, now=NOW)

        assert results == ()

    def test_a_recent_cyclone_position_is_evaluated(self) -> None:
        recent = picture(cyclones=(cyclone(age=timedelta(hours=2)),))

        results = evaluate_cyclones(recent, PALK_BAY_LAT, PALK_BAY_LON, now=NOW)

        assert len(results) == 2

    def test_stale_conditions_produce_unevaluated_not_clear(self, vallam: VesselProfile) -> None:
        """A proactive alert asserts something about *now*, without being asked."""
        stale = conditions(wave_m=0.5, age=timedelta(hours=5))

        result = check_wave_height(stale, vallam, now=NOW)

        assert result.outcome is TriggerOutcome.UNEVALUATED
        assert "too stale" in result.detail


class TestVesselRelativeThresholds:
    def test_the_same_sea_is_judged_differently_by_boat_class(
        self, vallam: VesselProfile, trawler: VesselProfile
    ) -> None:
        """2.2 m: unremarkable for a trawler, past the avoid limit for an open vallam.

        The gap is the whole argument for vessel-relative thresholds — a single sea-state
        band would have to either terrify the trawler or fail to warn the vallam.
        """
        sea = conditions(wave_m=2.2)

        assert check_wave_height(sea, vallam, now=NOW).outcome is TriggerOutcome.WARNING
        assert check_wave_height(sea, trawler, now=NOW).outcome is TriggerOutcome.CLEAR

    def test_the_caution_threshold_produces_a_watch(self, vallam: VesselProfile) -> None:
        result = check_wave_height(conditions(wave_m=1.6), vallam, now=NOW)

        assert result.outcome is TriggerOutcome.WATCH

    def test_below_caution_is_clear(self, vallam: VesselProfile) -> None:
        assert check_wave_height(conditions(wave_m=0.9), vallam, now=NOW).outcome is (
            TriggerOutcome.CLEAR
        )

    def test_the_threshold_source_names_the_vessel_class(self, vallam: VesselProfile) -> None:
        """An alert must be explainable as one sentence of arithmetic."""
        result = check_wave_height(conditions(wave_m=2.4), vallam, now=NOW)

        assert "frp_vallam" in result.threshold_source
        assert result.threshold["avoid_m"] == 2.0
        assert result.observed["significant_wave_height_m"] == 2.4

    def test_alert_thresholds_are_the_kernel_thresholds(self, vallam: VesselProfile) -> None:
        """A proactive alert and an answered question must not disagree about a boat."""
        result = check_wave_height(conditions(wave_m=2.4), vallam, now=NOW)

        assert result.threshold["avoid_m"] == vallam.thresholds.avoid_wave_height_m
        assert result.threshold["caution_m"] == vallam.thresholds.caution_wave_height_m

    def test_wind_is_judged_against_the_class_too(
        self, vallam: VesselProfile, trawler: VesselProfile
    ) -> None:
        wind = conditions(wind_ms=13.0)

        assert check_wind_speed(wind, vallam, now=NOW).outcome is TriggerOutcome.WARNING
        assert check_wind_speed(wind, trawler, now=NOW).outcome is TriggerOutcome.WATCH

    def test_a_missing_driver_is_unevaluated(self, vallam: VesselProfile) -> None:
        assert check_wave_height(conditions(wave_m=None), vallam, now=NOW).outcome is (
            TriggerOutcome.UNEVALUATED
        )
        assert check_wind_speed(conditions(wind_ms=None), vallam, now=NOW).outcome is (
            TriggerOutcome.UNEVALUATED
        )


class TestLightning:
    def test_a_few_strikes_are_a_watch(self, trawler: VesselProfile) -> None:
        assert check_lightning(conditions(strikes=4), trawler, now=NOW).outcome is (
            TriggerOutcome.WATCH
        )

    def test_many_strikes_warn_a_decked_boat(self, trawler: VesselProfile) -> None:
        assert check_lightning(conditions(strikes=12), trawler, now=NOW).outcome is (
            TriggerOutcome.WARNING
        )

    def test_an_open_hull_escalates_at_the_same_strike_count(
        self, vallam: VesselProfile, trawler: VesselProfile
    ) -> None:
        """An open boat is the tallest thing on a flat sea with nowhere to shelter."""
        storm = conditions(strikes=12)

        assert check_lightning(storm, vallam, now=NOW).outcome is TriggerOutcome.EMERGENCY
        assert check_lightning(storm, trawler, now=NOW).outcome is TriggerOutcome.WARNING

    def test_no_lightning_data_is_unevaluated_not_clear(self, vallam: VesselProfile) -> None:
        """The distinction this whole rule layer turns on."""
        result = check_lightning(conditions(strikes=None), vallam, now=NOW)

        assert result.outcome is TriggerOutcome.UNEVALUATED
        assert not result.fired

    def test_zero_strikes_is_genuinely_clear(self, vallam: VesselProfile) -> None:
        assert check_lightning(conditions(strikes=0), vallam, now=NOW).outcome is (
            TriggerOutcome.CLEAR
        )


class TestSquall:
    def test_a_coin_flip_probability_is_a_warning(self, vallam: VesselProfile) -> None:
        """Squalls capsize small craft before anything can be hauled; the bar is low."""
        assert check_squall(conditions(squall=0.55), vallam, now=NOW).outcome is (
            TriggerOutcome.WARNING
        )

    def test_a_moderate_probability_is_a_watch(self, vallam: VesselProfile) -> None:
        assert check_squall(conditions(squall=0.35), vallam, now=NOW).outcome is (
            TriggerOutcome.WATCH
        )

    def test_no_nowcast_is_unevaluated(self, vallam: VesselProfile) -> None:
        assert check_squall(conditions(squall=None), vallam, now=NOW).outcome is (
            TriggerOutcome.UNEVALUATED
        )


class TestGeofenceProximity:
    def proximity(self, band: WarningBand, distance_m: float) -> BoundaryProximity:
        return BoundaryProximity(
            lat=PALK_BAY_LAT,
            lon=PALK_BAY_LON,
            distance_metres=distance_m,
            side=BoundarySide.INDIA,
            band=band,
            nearest_lat=9.1,
            nearest_lon=79.4,
            bearing_to_boundary_deg=135.0,
        )

    @pytest.mark.parametrize(
        ("band", "expected"),
        [
            (WarningBand.CLEAR, TriggerOutcome.CLEAR),
            (WarningBand.AMBER, TriggerOutcome.WATCH),
            (WarningBand.RED, TriggerOutcome.WARNING),
            (WarningBand.CROSSED, TriggerOutcome.EMERGENCY),
        ],
    )
    def test_the_band_maps_to_an_outcome(
        self, band: WarningBand, expected: TriggerOutcome
    ) -> None:
        assert check_geofence_proximity(self.proximity(band, 1500)).outcome is expected

    def test_a_crossing_is_an_emergency(self) -> None:
        """The 2025 Palk Strait arrests are what this rule exists to prevent."""
        result = check_geofence_proximity(self.proximity(WarningBand.CROSSED, 800))

        assert result.outcome is TriggerOutcome.EMERGENCY

    def test_the_result_carries_the_distance_and_side(self) -> None:
        result = check_geofence_proximity(self.proximity(WarningBand.RED, 1500))

        assert result.observed["distance_km"] == 1.5
        assert result.observed["side"] == "india"

    def test_the_threshold_source_names_the_treaty_geometry(self) -> None:
        """Never a computed median line — METHODS.md §3 is explicit about that."""
        result = check_geofence_proximity(self.proximity(WarningBand.RED, 1500))

        assert "1974/76" in result.threshold_source


class TestPredictiveDrift:
    def forecast(self, minutes: float | None) -> DriftForecast:
        track = (
            ProjectedPosition(
                elapsed=timedelta(0),
                lat=PALK_BAY_LAT,
                lon=PALK_BAY_LON,
                distance_metres=4200.0,
                side=BoundarySide.INDIA,
                band=WarningBand.AMBER,
            ),
        )
        return DriftForecast(
            track=track,
            time_to_boundary=timedelta(minutes=minutes) if minutes is not None else None,
            crossing_lat=9.1 if minutes is not None else None,
            crossing_lon=79.4 if minutes is not None else None,
            horizon=timedelta(hours=1),
            assumptions=("constant current", "unchanged helm"),
        )

    def test_no_predicted_crossing_is_clear(self) -> None:
        assert check_predictive_drift(self.forecast(None)).outcome is TriggerOutcome.CLEAR

    def test_a_distant_crossing_is_a_watch(self) -> None:
        assert check_predictive_drift(self.forecast(45)).outcome is TriggerOutcome.WATCH

    def test_a_crossing_inside_twenty_minutes_is_a_warning(self) -> None:
        """Roughly the time to haul nets and turn."""
        assert check_predictive_drift(self.forecast(18)).outcome is TriggerOutcome.WARNING

    def test_a_crossing_inside_ten_minutes_is_an_emergency(self) -> None:
        assert check_predictive_drift(self.forecast(6)).outcome is TriggerOutcome.EMERGENCY

    def test_severity_tracks_time_remaining_not_distance(self) -> None:
        """A boat 3 km off closing fast is in more trouble than one 1 km off holding."""
        closing = check_predictive_drift(self.forecast(6))
        distant = check_predictive_drift(self.forecast(50))

        assert closing.rank > distant.rank

    def test_the_result_carries_the_predicted_crossing_point(self) -> None:
        result = check_predictive_drift(self.forecast(18))

        assert result.observed["time_to_boundary_minutes"] == 18.0
        assert result.observed["crossing_lat"] == 9.1

    def test_a_drifting_boat_with_engines_off_is_still_projected(self) -> None:
        """Speed zero is the Palk Strait case, not an edge case."""
        geofence = ImblGeofence()
        motion = VesselMotion.from_knots(0.0, 0.0, current_east_ms=0.5, current_north_ms=-0.5)

        forecast = DriftPredictor(geofence).project(
            PALK_BAY_LAT, PALK_BAY_LON, motion, horizon=timedelta(hours=2)
        )
        result = check_predictive_drift(forecast)

        assert result.trigger_id is TriggerId.GEOFENCE_DRIFT
        assert result.observed


class TestAggregation:
    def test_the_worst_fired_rule_is_selected(self, vallam: VesselProfile) -> None:
        results = evaluate_vessel_conditions(
            conditions(wave_m=2.4, wind_ms=9.0, strikes=4, squall=0.1), vallam, now=NOW
        )

        selected = worst(results)

        assert selected is not None
        assert selected.trigger_id is TriggerId.WAVE_HEIGHT

    def test_nothing_fired_yields_none(self, vallam: VesselProfile) -> None:
        results = evaluate_vessel_conditions(conditions(), vallam, now=NOW)

        assert worst(results) is None

    def test_rule_order_is_fixed(self, vallam: VesselProfile) -> None:
        """Stable order is what makes the audit diffable and the deduplication stable."""
        results = evaluate_vessel_conditions(conditions(), vallam, now=NOW)

        assert [r.trigger_id for r in results] == [
            TriggerId.WAVE_HEIGHT,
            TriggerId.WIND_SPEED,
            TriggerId.LIGHTNING,
            TriggerId.SQUALL,
        ]

    def test_evaluation_is_deterministic(self, vallam: VesselProfile) -> None:
        sea = conditions(wave_m=2.4, wind_ms=13.0, strikes=4, squall=0.35)

        outcomes = {
            tuple(r.outcome for r in evaluate_vessel_conditions(sea, vallam, now=NOW))
            for _ in range(20)
        }

        assert len(outcomes) == 1

    def test_two_active_cyclones_are_both_evaluated(self) -> None:
        both = picture(cyclones=(cyclone(name="Fengal"), cyclone(name="Montha")))

        results = evaluate_cyclones(both, PALK_BAY_LAT, PALK_BAY_LON, now=NOW)

        systems = {r.observed.get("system") for r in results}
        assert systems == {"Fengal", "Montha"}
