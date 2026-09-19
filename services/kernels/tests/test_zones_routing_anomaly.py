"""Pareto zones, geofence-constrained routing, anomalies and kernel provenance.

PLAN.md Phase 4.3-4.6. No database and no network: these kernels are arithmetic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orca_kernels import (
    AbstainReason,
    AnomalyKind,
    CostSurface,
    GridCell,
    InputRole,
    KernelInput,
    ZoneCandidate,
    detect_anomalies,
    detect_harmful_algal_bloom,
    detect_marine_heatwave,
    detect_oil_slick,
    dominates,
    great_circle_baseline,
    pareto_frontier,
    plan_route,
    rank_fishing_zones,
)

NOW = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)


def zone(zone_id: str, **overrides) -> ZoneCandidate:
    defaults = {
        "zone_id": zone_id,
        "lat": 9.5,
        "lon": 79.4,
        "distance_nm": 10.0,
        "fuel_cost_litres": 20.0,
        "safety_score": 75.0,
        "pfz_proximity_km": 5.0,
        "pfz_issued_at": NOW - timedelta(hours=12),
    }
    return ZoneCandidate(**(defaults | overrides))


class TestParetoNonDomination:
    def test_dominance_requires_at_least_as_good_everywhere(self) -> None:
        assert dominates((2.0, 2.0), (1.0, 1.0))
        assert not dominates((2.0, 0.0), (1.0, 1.0))
        assert not dominates((1.0, 1.0), (1.0, 1.0)), "equal vectors do not dominate"

    def test_mismatched_vectors_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            dominates((1.0, 2.0), (1.0,))

    def test_a_strictly_worse_zone_is_excluded(self) -> None:
        good = zone("good", distance_nm=8.0, fuel_cost_litres=15.0, safety_score=85.0)
        worse = zone("worse", distance_nm=20.0, fuel_cost_litres=40.0, safety_score=60.0)

        frontier = pareto_frontier([good, worse], now=NOW)

        assert [z.zone_id for z in frontier] == ["good"]

    def test_a_trade_off_keeps_both_options(self) -> None:
        """Closer but poorer fishing vs further but better: both are defensible."""
        near = zone("near", distance_nm=5.0, fuel_cost_litres=10.0, pfz_proximity_km=40.0)
        far = zone("far", distance_nm=25.0, fuel_cost_litres=50.0, pfz_proximity_km=1.0)

        frontier = pareto_frontier([near, far], now=NOW)

        assert {z.zone_id for z in frontier} == {"near", "far"}

    def test_no_zone_on_the_frontier_dominates_another(self) -> None:
        candidates = [
            zone("a", distance_nm=5.0, fuel_cost_litres=10.0, pfz_proximity_km=40.0),
            zone("b", distance_nm=15.0, fuel_cost_litres=30.0, pfz_proximity_km=10.0),
            zone("c", distance_nm=25.0, fuel_cost_litres=50.0, pfz_proximity_km=1.0),
            zone("dominated", distance_nm=30.0, fuel_cost_litres=60.0, pfz_proximity_km=45.0),
        ]

        frontier = pareto_frontier(candidates, now=NOW)

        for a in frontier:
            for b in frontier:
                if a.zone_id != b.zone_id:
                    assert not dominates(a.objectives(NOW), b.objectives(NOW))
        assert "dominated" not in {z.zone_id for z in frontier}

    def test_frontier_order_is_deterministic(self) -> None:
        candidates = [
            zone(f"z{i}", distance_nm=5.0 + i, pfz_proximity_km=float(i)) for i in range(5)
        ]

        first = pareto_frontier(candidates, now=NOW)
        second = pareto_frontier(list(reversed(candidates)), now=NOW)

        assert [z.zone_id for z in first] == [z.zone_id for z in second]


class TestZoneRanking:
    def test_a_frontier_is_returned_not_a_single_point(self) -> None:
        """METHODS.md §1: return the trade-off, never one answer."""
        result = rank_fishing_zones(
            candidates=[
                zone("near", distance_nm=5.0, fuel_cost_litres=10.0, pfz_proximity_km=40.0),
                zone("far", distance_nm=25.0, fuel_cost_litres=50.0, pfz_proximity_km=1.0),
            ],
            evaluated_at=NOW,
        )

        assert not result.abstained
        assert len(result.extras["frontier"]) == 2

    def test_non_compliant_zones_are_removed_before_ranking(self) -> None:
        """A zone across the IMBL is not a cheaper option with a caveat."""
        result = rank_fishing_zones(
            candidates=[
                zone("legal"),
                zone(
                    "across_imbl",
                    distance_nm=2.0,
                    fuel_cost_litres=5.0,
                    pfz_proximity_km=0.5,
                    compliant=False,
                    compliance_detail="lies across the India-Sri Lanka IMBL",
                ),
            ],
            evaluated_at=NOW,
        )

        returned = {z["zone_id"] for z in result.extras["frontier"]}
        assert returned == {"legal"}
        assert result.extras["excluded_by_constraint"][0]["zone_id"] == "across_imbl"

    def test_all_zones_non_compliant_abstains(self) -> None:
        result = rank_fishing_zones(
            candidates=[zone("a", compliant=False, compliance_detail="inside a no-take MPA")],
            evaluated_at=NOW,
        )

        assert result.abstain_reason is AbstainReason.CONSTRAINT_VIOLATION
        assert "no-take MPA" in (result.detail or "")

    def test_no_candidates_abstains(self) -> None:
        result = rank_fishing_zones(candidates=[], evaluated_at=NOW)
        assert result.abstain_reason is AbstainReason.INSUFFICIENT_EVIDENCE

    def test_stale_pfz_advisory_loses_weight(self) -> None:
        """PFZ is issued 3x/week; a two-cycle-old advisory is weak evidence."""
        fresh = zone("fresh", pfz_issued_at=NOW - timedelta(hours=6))
        stale = zone("stale", pfz_issued_at=NOW - timedelta(hours=120))

        assert fresh.pfz_weight(NOW) == 1.0
        assert stale.pfz_weight(NOW) == 0.0
        assert fresh.catch_signal(NOW) > stale.catch_signal(NOW)

    def test_a_zone_without_a_pfz_advisory_is_not_credited_with_one(self) -> None:
        bare = zone("bare", pfz_proximity_km=None, pfz_issued_at=None)
        assert bare.pfz_weight(NOW) == 0.0

    def test_options_are_capped_and_spread(self) -> None:
        """Three near-identical zones would hide the trade-off, not show it."""
        candidates = [
            zone("cheap", distance_nm=3.0, fuel_cost_litres=6.0, pfz_proximity_km=45.0),
            zone("safe", distance_nm=12.0, fuel_cost_litres=25.0, safety_score=95.0),
            zone("fishy", distance_nm=28.0, fuel_cost_litres=55.0, pfz_proximity_km=0.5),
            zone("mid", distance_nm=15.0, fuel_cost_litres=30.0, pfz_proximity_km=20.0),
        ]

        result = rank_fishing_zones(candidates=candidates, evaluated_at=NOW, max_options=3)

        assert len(result.extras["frontier"]) == 3


class TestGeofenceConstrainedRouting:
    def _grid(self, blocked: set[tuple[int, int]] | None = None, **cell_kwargs) -> CostSurface:
        blocked = blocked or set()
        cells = [
            GridCell(
                row=row,
                col=col,
                lat=9.0 + row * 0.1,
                lon=79.0 + col * 0.1,
                blocked=(row, col) in blocked,
                blocked_reason="IMBL buffer" if (row, col) in blocked else None,
                **cell_kwargs,
            )
            for row in range(5)
            for col in range(5)
        ]
        return CostSurface(cells)

    def test_a_route_is_found_across_an_open_grid(self) -> None:
        result = plan_route(surface=self._grid(), start=(0, 0), goal=(4, 4), evaluated_at=NOW)

        assert not result.abstained
        assert result.value is not None
        waypoints = result.extras["waypoints"]
        assert waypoints[0]["lat"] == pytest.approx(9.0)
        assert waypoints[-1]["lat"] == pytest.approx(9.4)

    def test_the_route_never_enters_a_blocked_cell(self) -> None:
        """Geofences are removals, not penalties: no weather makes crossing acceptable."""
        wall = {(r, 2) for r in range(4)}  # wall with a gap at row 4
        surface = self._grid(blocked=wall)

        result = plan_route(surface=surface, start=(0, 0), goal=(0, 4), evaluated_at=NOW)

        assert not result.abstained
        blocked_coords = {(round(9.0 + r * 0.1, 4), round(79.0 + 2 * 0.1, 4)) for r in range(4)}
        for point in result.extras["waypoints"]:
            assert (round(point["lat"], 4), round(point["lon"], 4)) not in blocked_coords

    def test_a_fully_blocked_corridor_abstains_rather_than_routing_through(self) -> None:
        wall = {(r, 2) for r in range(5)}
        surface = self._grid(blocked=wall)

        result = plan_route(surface=surface, start=(0, 0), goal=(0, 4), evaluated_at=NOW)

        assert result.abstain_reason is AbstainReason.CONSTRAINT_VIOLATION
        assert "IMBL buffer" in (result.detail or "")

    def test_starting_inside_a_blocked_cell_abstains(self) -> None:
        surface = self._grid(blocked={(0, 0)})

        result = plan_route(surface=surface, start=(0, 0), goal=(4, 4), evaluated_at=NOW)

        assert result.abstain_reason is AbstainReason.CONSTRAINT_VIOLATION

    def test_off_grid_endpoints_abstain(self) -> None:
        result = plan_route(surface=self._grid(), start=(0, 0), goal=(99, 99), evaluated_at=NOW)
        assert result.abstain_reason is AbstainReason.OUT_OF_COVERAGE

    def test_the_router_prefers_a_calm_corridor_of_equal_length(self) -> None:
        """Cost, not just legality: given two equal-length paths, take the calmer one.

        Equal length is the point — it isolates sea state as the only difference, so the
        assertion cannot be satisfied by a shorter route instead of a calmer one.
        """
        cells = [
            GridCell(
                row=row,
                col=col,
                lat=9.0 + row * 0.1,
                lon=79.0 + col * 0.1,
                wave_height_m=3.5 if row == 0 else 0.2,
            )
            for row in range(3)
            for col in range(5)
        ]
        surface = CostSurface(cells)

        result = plan_route(surface=surface, start=(0, 0), goal=(0, 4), evaluated_at=NOW)

        assert not result.abstained
        rough_lat = round(9.0, 4)
        interior = result.extras["waypoints"][1:-1]
        assert all(round(p["lat"], 4) != rough_lat for p in interior), (
            "route should drop out of the rough row for the crossing"
        )

    def test_a_severe_band_is_worth_a_long_detour(self) -> None:
        """When the weather is bad enough, the detour must win on cost alone.

        A mild band is genuinely not worth a long detour — verified separately: with a
        4 m band, crossing costs 13.2 against a 13.52 detour, and the router correctly
        crosses. This asserts the behaviour where avoidance really is optimal.
        """
        cells = [
            GridCell(
                row=row,
                col=col,
                lat=9.0 + row * 0.1,
                lon=79.0 + col * 0.1,
                wave_height_m=12.0 if (col == 2 and row < 4) else 0.2,
            )
            for row in range(5)
            for col in range(5)
        ]
        surface = CostSurface(cells)

        result = plan_route(surface=surface, start=(0, 0), goal=(0, 4), evaluated_at=NOW)

        assert not result.abstained
        severe = {(round(9.0 + r * 0.1, 4), round(79.2, 4)) for r in range(4)}
        for point in result.extras["waypoints"]:
            assert (round(point["lat"], 4), round(point["lon"], 4)) not in severe

    def test_following_current_is_cheaper_than_head_current(self) -> None:
        def surface_with(current_east: float) -> CostSurface:
            return CostSurface(
                GridCell(
                    row=row,
                    col=col,
                    lat=9.0 + row * 0.1,
                    lon=79.0 + col * 0.1,
                    current_east_ms=current_east,
                )
                for row in range(3)
                for col in range(3)
            )

        following = plan_route(
            surface=surface_with(1.5), start=(0, 0), goal=(0, 2), evaluated_at=NOW
        )
        against = plan_route(
            surface=surface_with(-1.5), start=(0, 0), goal=(0, 2), evaluated_at=NOW
        )

        assert following.value is not None and against.value is not None
        assert following.value < against.value

    def test_shallow_water_is_excluded_for_a_deep_draught_vessel(self) -> None:
        cells = [
            GridCell(
                row=row,
                col=col,
                lat=9.0 + row * 0.1,
                lon=79.0 + col * 0.1,
                depth_m=2.0 if col == 2 else 30.0,
            )
            for row in range(3)
            for col in range(5)
        ]
        surface = CostSurface(cells, min_depth_m=5.0)

        result = plan_route(surface=surface, start=(0, 0), goal=(0, 4), evaluated_at=NOW)

        assert result.abstain_reason is AbstainReason.CONSTRAINT_VIOLATION

    def test_astar_beats_or_matches_the_great_circle_baseline(self) -> None:
        """The benchmark PLAN.md 4.4 asks for, against naive straight-line routing."""
        cells = []
        for row in range(6):
            for col in range(6):
                rough = col == 3 and row < 5
                cells.append(
                    GridCell(
                        row=row,
                        col=col,
                        lat=9.0 + row * 0.1,
                        lon=79.0 + col * 0.1,
                        wave_height_m=3.5 if rough else 0.3,
                    )
                )
        surface = CostSurface(cells)

        optimised = plan_route(surface=surface, start=(0, 0), goal=(0, 5), evaluated_at=NOW)
        naive = great_circle_baseline(surface=surface, start=(0, 0), goal=(0, 5))

        assert optimised.value is not None and naive is not None
        assert optimised.value <= naive

    def test_great_circle_returns_none_when_it_would_cross_a_geofence(self) -> None:
        """The naive route is not merely dearer — it is often illegal."""
        surface = self._grid(blocked={(0, 2)})
        assert great_circle_baseline(surface=surface, start=(0, 0), goal=(0, 4)) is None


class TestAnomalyDetection:
    def test_chlorophyll_spike_flags_a_possible_bloom(self) -> None:
        flag = detect_harmful_algal_bloom(chlorophyll_mg_m3=6.0, baseline_mg_m3=1.0)

        assert flag is not None
        assert flag.kind is AnomalyKind.HARMFUL_ALGAL_BLOOM
        assert "consistent with" in flag.detail
        assert "ABIS" in flag.detail

    def test_a_high_ratio_on_a_tiny_baseline_is_not_a_bloom(self) -> None:
        """Three times a negligible baseline is still negligible."""
        assert detect_harmful_algal_bloom(chlorophyll_mg_m3=0.3, baseline_mg_m3=0.05) is None

    def test_normal_chlorophyll_raises_nothing(self) -> None:
        assert detect_harmful_algal_bloom(chlorophyll_mg_m3=1.2, baseline_mg_m3=1.0) is None

    def test_five_consecutive_hot_days_is_a_marine_heatwave(self) -> None:
        flag = detect_marine_heatwave(
            daily_sst_c=[30.2, 30.4, 30.5, 30.3, 30.6, 29.0], climatology_p90_c=30.0
        )

        assert flag is not None
        assert flag.evidence["consecutive_days"] == 5.0

    def test_four_hot_days_is_not(self) -> None:
        assert (
            detect_marine_heatwave(
                daily_sst_c=[30.2, 30.4, 30.5, 30.3, 29.0, 29.1], climatology_p90_c=30.0
            )
            is None
        )

    def test_non_consecutive_hot_days_do_not_count(self) -> None:
        assert (
            detect_marine_heatwave(
                daily_sst_c=[30.2, 29.0, 30.4, 29.1, 30.5, 29.2, 30.6], climatology_p90_c=30.0
            )
            is None
        )

    def test_slick_flag_is_always_low_confidence(self) -> None:
        """Ocean colour cannot distinguish a slick from sun glint; SAR can, and we lack it."""
        flag = detect_oil_slick(chlorophyll_mg_m3=0.01, baseline_mg_m3=0.8)

        assert flag is not None
        assert flag.confidence.value == "low"
        assert "SAR" in flag.detail

    def test_no_inputs_at_all_abstains(self) -> None:
        result = detect_anomalies(evaluated_at=NOW)

        assert result.abstain_reason is AbstainReason.MISSING_REQUIRED_INPUT

    def test_nothing_detected_is_a_result_not_an_abstention(self) -> None:
        """'Looked and found nothing' differs from 'could not look'."""
        result = detect_anomalies(
            evaluated_at=NOW, chlorophyll_mg_m3=1.1, chlorophyll_baseline_mg_m3=1.0
        )

        assert not result.abstained
        assert result.value == 0.0
        assert result.extras["flags"] == []

    def test_multiple_flags_are_reported_together(self) -> None:
        result = detect_anomalies(
            evaluated_at=NOW,
            chlorophyll_mg_m3=8.0,
            chlorophyll_baseline_mg_m3=1.0,
            daily_sst_c=[30.5] * 6,
            climatology_p90_c=30.0,
        )

        kinds = {flag["kind"] for flag in result.extras["flags"]}
        assert kinds == {"harmful_algal_bloom", "marine_heatwave"}


class TestKernelProvenance:
    """PLAN.md 4.6: every result carries value, inputs, formula id/version, staleness."""

    def _result(self):
        return plan_route(
            surface=CostSurface(
                GridCell(row=r, col=c, lat=9.0 + r * 0.1, lon=79.0 + c * 0.1)
                for r in range(3)
                for c in range(3)
            ),
            start=(0, 0),
            goal=(2, 2),
            evaluated_at=NOW,
            inputs=(
                KernelInput(
                    name="significant_wave_height",
                    value=1.2,
                    unit="m",
                    source="cmems",
                    issued_time=NOW - timedelta(hours=2),
                    provenance_id="cmems:vhm0:1",
                    role=InputRole.REQUIRED,
                    age_seconds=7200,
                ),
            ),
        )

    def test_every_kernel_declares_formula_identity(self) -> None:
        for result in (
            self._result(),
            rank_fishing_zones(candidates=[zone("a")], evaluated_at=NOW),
            detect_anomalies(
                evaluated_at=NOW, chlorophyll_mg_m3=1.0, chlorophyll_baseline_mg_m3=1.0
            ),
        ):
            assert result.formula_id.startswith("orca.")
            assert result.formula_version.count(".") == 2
            assert result.kernel

    def test_inputs_and_staleness_travel_with_the_result(self) -> None:
        result = self._result()

        assert result.input_named("significant_wave_height") is not None
        assert result.staleness.max_age_hours == pytest.approx(2.0)
        assert result.staleness.evaluated_at == NOW

    def test_fingerprint_is_stable_across_recomputation(self) -> None:
        assert self._result().verify(self._result())

    def test_a_tampered_value_fails_verification(self) -> None:
        original = self._result()
        tampered = original.model_copy(update={"value": 0.01})

        assert not original.verify(tampered)

    def test_abstention_must_explain_itself(self) -> None:
        from pydantic import ValidationError

        from orca_kernels import KernelResult, Staleness

        with pytest.raises(ValidationError, match="explain itself"):
            KernelResult(
                kernel="k",
                formula_id="orca.test",
                formula_version="1.0.0",
                value=None,
                unit="u",
                inputs=(),
                staleness=Staleness(max_age_seconds=0, evaluated_at=NOW),
                abstain_reason=AbstainReason.STALE_INPUT,
            )

    def test_an_abstaining_result_cannot_carry_a_value(self) -> None:
        from pydantic import ValidationError

        from orca_kernels import KernelResult, Staleness

        with pytest.raises(ValidationError, match="must not return a value"):
            KernelResult(
                kernel="k",
                formula_id="orca.test",
                formula_version="1.0.0",
                value=42.0,
                unit="u",
                inputs=(),
                staleness=Staleness(max_age_seconds=0, evaluated_at=NOW),
                abstain_reason=AbstainReason.STALE_INPUT,
                detail="stale",
            )

    def test_a_missing_value_requires_an_abstain_reason(self) -> None:
        from pydantic import ValidationError

        from orca_kernels import KernelResult, Staleness

        with pytest.raises(ValidationError, match="requires an abstain_reason"):
            KernelResult(
                kernel="k",
                formula_id="orca.test",
                formula_version="1.0.0",
                value=None,
                unit="u",
                inputs=(),
                staleness=Staleness(max_age_seconds=0, evaluated_at=NOW),
            )
