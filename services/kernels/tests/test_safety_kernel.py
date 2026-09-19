"""Boat-relative safety score, per vessel class and under stale input (PLAN.md 4.2).

The two properties that matter most are asserted directly:

* **the same sea state produces different verdicts for different boats** — the Tier-1
  differentiator, and the thing a generic colour-coded sea-state band cannot do;
* **a stale required input produces no score at all** — a safety verdict computed from
  yesterday's forecast is worse than silence, because the fisherman cannot tell them apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orca_kernels import (
    AbstainReason,
    InputRole,
    KernelInput,
    SafetyDrivers,
    VesselClass,
    VesselProfile,
    band_for,
    compute_safety_score,
    normalise_hazard,
    swell_period_hazard,
)
from orca_kernels.safety import FORMULA_ID, FORMULA_VERSION, MIN_RELIABILITY

NOW = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)
ONE_HOUR_AGO = NOW - timedelta(hours=1)


def vessel(vessel_class: VesselClass, **overrides) -> VesselProfile:
    defaults = {
        "vessel_id": f"boat-{vessel_class.value}",
        "vessel_class": vessel_class,
        "length_overall_m": 9.0,
        "engine_power_hp": 25.0,
        "range_nm": 30.0,
        "freeboard_m": 0.72,  # 0.08 * LOA, so margin_factor == 1.0
        "crew_size": 3,
        "home_port": "Rameswaram",
    }
    return VesselProfile(**(defaults | overrides))


def driver(name: str, value: float | None, unit: str, *, stale: bool = False, age_h: float = 1.0):
    return KernelInput(
        name=name,
        value=value,
        unit=unit,
        source="cmems",
        issued_time=NOW - timedelta(hours=age_h),
        provenance_id=f"cmems:{name}",
        role=InputRole.REQUIRED,
        stale=stale,
        age_seconds=age_h * 3600,
    )


def drivers(wave: float, wind: float, **kwargs) -> SafetyDrivers:
    return SafetyDrivers(
        wave_height_m=driver("significant_wave_height", wave, "m", **kwargs),
        wind_speed_ms=driver("wind_speed", wind, "m s-1"),
    )


class TestBoatRelativeVerdicts:
    """The headline claim: the boat changes the answer."""

    def test_same_sea_state_is_safe_for_a_trawler_and_not_for_a_catamaran(self) -> None:
        """METHODS.md §1: 2.5 m is routine for a trawler and lethal for a small craft."""
        sea = drivers(wave=2.5, wind=9.0)

        trawler = compute_safety_score(
            vessel=vessel(VesselClass.MECHANIZED_TRAWLER),
            drivers=sea,
            reliability=0.9,
            evaluated_at=NOW,
        )
        catamaran = compute_safety_score(
            vessel=vessel(VesselClass.CATAMARAN),
            drivers=sea,
            reliability=0.9,
            evaluated_at=NOW,
        )

        assert trawler.value is not None and catamaran.value is not None
        assert trawler.value > catamaran.value
        assert catamaran.extras["band"] == "avoid"

    @pytest.mark.parametrize(
        ("vessel_class", "expected_order"),
        [
            (VesselClass.CATAMARAN, 0),
            (VesselClass.FRP_VALLAM, 1),
            (VesselClass.GILLNETTER, 2),
            (VesselClass.MECHANIZED_TRAWLER, 3),
        ],
    )
    def test_scores_increase_with_seaworthiness(self, vessel_class, expected_order) -> None:
        result = compute_safety_score(
            vessel=vessel(vessel_class),
            drivers=drivers(wave=2.0, wind=9.0),
            reliability=0.9,
            evaluated_at=NOW,
        )
        assert result.value is not None
        assert result.extras["vessel_class"] == vessel_class.value

    def test_ordering_across_all_four_classes_is_monotonic(self) -> None:
        sea = drivers(wave=2.0, wind=9.0)
        scores = [
            compute_safety_score(
                vessel=vessel(c), drivers=sea, reliability=0.9, evaluated_at=NOW
            ).value
            for c in (
                VesselClass.CATAMARAN,
                VesselClass.FRP_VALLAM,
                VesselClass.GILLNETTER,
                VesselClass.MECHANIZED_TRAWLER,
            )
        ]
        assert scores == sorted(scores), f"expected increasing seaworthiness, got {scores}"

    def test_frp_vallam_thresholds_match_the_documented_anchor(self) -> None:
        """METHODS.md §1: FRP vallam Hs < 1.5 m caution, < 2 m avoid."""
        boat = vessel(VesselClass.FRP_VALLAM)
        assert boat.thresholds.caution_wave_height_m == 1.5
        assert boat.thresholds.avoid_wave_height_m == 2.0

    def test_calm_conditions_score_high_for_every_class(self) -> None:
        for vessel_class in VesselClass:
            result = compute_safety_score(
                vessel=vessel(vessel_class),
                drivers=drivers(wave=0.3, wind=3.0),
                reliability=0.95,
                evaluated_at=NOW,
            )
            assert result.value is not None
            assert result.value >= 90.0, vessel_class
            assert result.extras["band"] == "safe"

    def test_extreme_conditions_score_low_for_every_class(self) -> None:
        for vessel_class in VesselClass:
            result = compute_safety_score(
                vessel=vessel(vessel_class),
                drivers=drivers(wave=6.0, wind=25.0),
                reliability=0.95,
                evaluated_at=NOW,
            )
            assert result.value is not None
            assert result.value <= 10.0, vessel_class
            assert result.extras["band"] == "avoid"

    def test_higher_freeboard_earns_a_small_margin(self) -> None:
        sea = drivers(wave=1.8, wind=9.0)
        standard = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=sea,
            reliability=0.9,
            evaluated_at=NOW,
        )
        high_sided = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM, freeboard_m=1.1),
            drivers=sea,
            reliability=0.9,
            evaluated_at=NOW,
        )

        assert standard.value is not None and high_sided.value is not None
        assert high_sided.value > standard.value
        # Bounded: freeboard must not be able to turn an unsafe sea into a safe one.
        assert high_sided.value - standard.value < 15.0


class TestStaleInputAbstention:
    """A safety verdict on stale data is worse than no verdict."""

    def test_stale_wave_height_forces_abstention(self) -> None:
        result = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=drivers(wave=1.0, wind=5.0, stale=True, age_h=14.0),
            reliability=0.9,
            evaluated_at=NOW,
        )

        assert result.abstained
        assert result.value is None
        assert result.abstain_reason is AbstainReason.STALE_INPUT
        assert "14.0 h old" in (result.detail or "")

    def test_abstention_names_the_stale_input_and_its_source(self) -> None:
        result = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=drivers(wave=1.0, wind=5.0, stale=True, age_h=9.0),
            reliability=0.9,
            evaluated_at=NOW,
        )

        assert "significant_wave_height" in (result.detail or "")
        assert "cmems" in (result.detail or "")
        assert result.staleness.stale_inputs == ("significant_wave_height",)

    def test_missing_required_input_abstains_distinctly_from_stale(self) -> None:
        """'Not registered' and 'too old' call for different responses."""
        result = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=SafetyDrivers(
                wave_height_m=driver("significant_wave_height", None, "m"),
                wind_speed_ms=driver("wind_speed", 5.0, "m s-1"),
            ),
            reliability=0.9,
            evaluated_at=NOW,
        )

        assert result.abstain_reason is AbstainReason.MISSING_REQUIRED_INPUT

    def test_unreliable_forecast_abstains_rather_than_scoring_confidently(self) -> None:
        result = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=drivers(wave=1.0, wind=5.0),
            reliability=MIN_RELIABILITY - 0.05,
            evaluated_at=NOW,
            lead_time_hours=120,
        )

        assert result.abstain_reason is AbstainReason.INSUFFICIENT_EVIDENCE
        assert "reliability" in (result.detail or "")

    def test_a_fresh_optional_input_missing_does_not_abstain(self) -> None:
        """Losing lightning data should widen the band, not silence the kernel."""
        result = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=drivers(wave=1.0, wind=5.0),
            reliability=0.9,
            evaluated_at=NOW,
        )

        assert not result.abstained
        assert result.confidence is not None
        assert "drivers unavailable" in result.confidence.basis


class TestConfidenceBand:
    def test_lower_reliability_widens_the_band(self) -> None:
        sea = drivers(wave=1.0, wind=6.0)
        confident = compute_safety_score(
            vessel=vessel(VesselClass.GILLNETTER), drivers=sea, reliability=0.95, evaluated_at=NOW
        )
        unsure = compute_safety_score(
            vessel=vessel(VesselClass.GILLNETTER), drivers=sea, reliability=0.4, evaluated_at=NOW
        )

        assert confident.confidence is not None and unsure.confidence is not None
        assert unsure.confidence.width > confident.confidence.width

    def test_band_never_leaves_the_score_range(self) -> None:
        result = compute_safety_score(
            vessel=vessel(VesselClass.MECHANIZED_TRAWLER),
            drivers=drivers(wave=0.1, wind=1.0),
            reliability=0.3,
            evaluated_at=NOW,
        )
        assert result.confidence is not None
        assert result.confidence.upper <= 100.0
        assert result.confidence.lower >= 0.0

    def test_a_boat_that_cannot_call_for_help_gets_a_wider_band(self) -> None:
        from orca_kernels import SafetyEquipment

        sea = drivers(wave=1.0, wind=6.0)
        silent = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=sea,
            reliability=0.9,
            evaluated_at=NOW,
        )
        equipped = compute_safety_score(
            vessel=vessel(
                VesselClass.FRP_VALLAM,
                equipment=frozenset({SafetyEquipment.VHF_RADIO}),
            ),
            drivers=sea,
            reliability=0.9,
            evaluated_at=NOW,
        )

        assert silent.confidence is not None and equipped.confidence is not None
        assert silent.confidence.width > equipped.confidence.width
        assert "no means of calling for help" in silent.confidence.basis


class TestHazardNormalisation:
    def test_below_caution_is_zero_hazard(self) -> None:
        assert normalise_hazard(1.0, 1.5, 2.0) == 0.0

    def test_at_or_beyond_avoid_is_full_hazard(self) -> None:
        assert normalise_hazard(2.0, 1.5, 2.0) == 1.0
        assert normalise_hazard(5.0, 1.5, 2.0) == 1.0

    def test_interpolates_between_thresholds(self) -> None:
        """A boat 10 cm under the avoid line must not be told the sea is fine."""
        assert normalise_hazard(1.75, 1.5, 2.0) == pytest.approx(0.5)

    def test_invalid_thresholds_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="must exceed"):
            normalise_hazard(1.0, 2.0, 1.5)

    def test_short_swell_period_raises_hazard(self) -> None:
        assert swell_period_hazard(8.0, 6.0) == 0.0
        assert swell_period_hazard(3.0, 6.0) == 1.0
        assert 0.0 < swell_period_hazard(4.5, 6.0) < 1.0


class TestBands:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [(100.0, "safe"), (70.0, "safe"), (69.9, "caution"), (40.0, "caution"), (39.9, "avoid")],
    )
    def test_band_thresholds(self, score: float, expected: str) -> None:
        assert band_for(score) == expected


class TestProvenance:
    def test_result_carries_formula_identity(self) -> None:
        result = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=drivers(wave=1.0, wind=5.0),
            reliability=0.9,
            evaluated_at=NOW,
        )

        assert result.formula_id == FORMULA_ID
        assert result.formula_version == FORMULA_VERSION
        assert result.kernel == "marine_safety_score"

    def test_every_input_is_recorded_with_its_source(self) -> None:
        result = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=drivers(wave=1.0, wind=5.0),
            reliability=0.9,
            evaluated_at=NOW,
        )

        names = {i.name for i in result.inputs}
        assert {"significant_wave_height", "wind_speed", "reliability"} <= names
        wave = result.input_named("significant_wave_height")
        assert wave is not None
        assert wave.source == "cmems"
        assert wave.provenance_id == "cmems:significant_wave_height"

    def test_identical_inputs_reproduce_the_same_result(self) -> None:
        """The basis of replay: same formula, same inputs, same fingerprint."""
        args = {
            "vessel": vessel(VesselClass.FRP_VALLAM),
            "drivers": drivers(wave=1.4, wind=7.0),
            "reliability": 0.85,
            "evaluated_at": NOW,
        }
        first = compute_safety_score(**args)
        second = compute_safety_score(**args)

        assert first.value == second.value
        assert first.verify(second)

    def test_a_changed_value_breaks_the_fingerprint(self) -> None:
        """If an LLM rewrote a score, the recorded inputs would no longer reproduce it."""
        original = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=drivers(wave=1.4, wind=7.0),
            reliability=0.85,
            evaluated_at=NOW,
        )
        tampered = original.model_copy(update={"value": 99.0})

        assert not original.verify(tampered)

    def test_staleness_reports_the_oldest_input(self) -> None:
        result = compute_safety_score(
            vessel=vessel(VesselClass.FRP_VALLAM),
            drivers=SafetyDrivers(
                wave_height_m=driver("significant_wave_height", 1.0, "m", age_h=2.0),
                wind_speed_ms=driver("wind_speed", 5.0, "m s-1"),
            ),
            reliability=0.9,
            evaluated_at=NOW,
        )

        assert result.staleness.max_age_hours == pytest.approx(2.0)
        assert result.staleness.evaluated_at == NOW
