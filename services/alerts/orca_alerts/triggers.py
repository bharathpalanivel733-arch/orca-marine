"""Deterministic alert triggers (PLAN.md Phase 8.1, 8.2).

**Every alert ORCA sends is produced by one of the rules in this file, and every CAP
severity, urgency and certainty comes out of a table here.** No language model decides
whether to warn somebody, how urgent it is, or how sure to sound. That is the same rule
Phase 4 applies to numbers, carried into the one part of the system that speaks without
being asked — and it matters more here, because an unsolicited warning is the output a user
cannot choose not to receive.

Each rule returns a :class:`TriggerResult` recording the observed value, the threshold it
was compared against, and where that threshold came from. An alert is therefore always
explainable as one sentence of arithmetic: *"wave height 2.8 m exceeds the 2.0 m avoid
threshold for an FRP vallam"*. A fisherman who has been woken at 4 a.m. is owed that.

Three rule families, matching PLAN.md 8.1:

* **(a) Cyclone containment** — the vessel is inside an IMD wind radius or the cone of
  uncertainty. Relayed on IMD's authority, never authored by ORCA.
* **(b) Vessel-relative thresholds** — wave height, wind, lightning and squall, judged
  against *this boat's* class thresholds. A 2.2 m sea is a routine afternoon for a trawler
  and a mortal risk for a catamaran, so a single sea-state band for everyone is the wrong
  abstraction.
* **(c) Geofence proximity and predictive drift** — distance to the IMBL and time to
  crossing, from the Phase 2 geometry.

A rule with a missing driver returns ``UNEVALUATED``, never "safe". Not seeing lightning
data is not the same as seeing no lightning, and collapsing the two is how a system stays
quiet through the one storm it existed for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from orca_geo import BoundaryProximity, DriftForecast, WarningBand
from orca_kernels import VesselProfile

from orca_alerts.conditions import CycloneSystem, HazardPicture, MarineConditions
from orca_alerts.subscriber import Subscriber

# --------------------------------------------------------------------------------------
# Thresholds that are not vessel-relative.
#
# Each is an engineering judgement, set near the point where it would change what a skipper
# does, and each is named here rather than buried in a comparison so it can be reviewed and
# argued with. None is a sourced constant; PROGRESS.md records that.
# --------------------------------------------------------------------------------------

# Cloud-to-ground strikes within 25 km. An open FRP boat has no lightning protection at all,
# so the bar is low: a handful of strikes nearby is already a reason to head in.
LIGHTNING_WATCH_STRIKES = 3
LIGHTNING_WARNING_STRIKES = 10

# Squall probability from the nowcast. Squalls capsize small craft faster than any other
# common hazard, which is why the warning bar sits at a bare coin flip.
SQUALL_WATCH_PROBABILITY = 0.3
SQUALL_WARNING_PROBABILITY = 0.5

# Predictive drift: how much warning is useful. Twenty minutes is roughly the time to haul
# nets and turn; below ten minutes the alert is nearly a notification of a crossing.
DRIFT_WARNING_HORIZON = timedelta(minutes=20)
DRIFT_IMMEDIATE_HORIZON = timedelta(minutes=10)

# Conditions older than this cannot support an alert. Tighter than the 12 h the trust layer
# allows for an answered question, because a proactive alert asserts something about *now*
# without being asked.
MAX_CONDITION_AGE = timedelta(hours=3)


class TriggerId(StrEnum):
    """The named rules. One alert, one rule — never a blended score."""

    CYCLONE_WIND = "cyclone.wind_radius"
    CYCLONE_CONE = "cyclone.cone_of_uncertainty"
    WAVE_HEIGHT = "vessel.wave_height"
    WIND_SPEED = "vessel.wind_speed"
    LIGHTNING = "vessel.lightning"
    SQUALL = "vessel.squall"
    GEOFENCE_PROXIMITY = "geofence.proximity"
    GEOFENCE_DRIFT = "geofence.predictive_drift"


class TriggerOutcome(StrEnum):
    """What a rule concluded.

    ``UNEVALUATED`` is distinct from ``CLEAR`` on purpose and throughout: the first means
    ORCA could not look, the second means it looked and found nothing.
    """

    CLEAR = "clear"
    WATCH = "watch"
    WARNING = "warning"
    EMERGENCY = "emergency"
    UNEVALUATED = "unevaluated"


# Ranked so escalation is a comparison rather than a chain of conditionals.
OUTCOME_RANK: dict[TriggerOutcome, int] = {
    TriggerOutcome.UNEVALUATED: -1,
    TriggerOutcome.CLEAR: 0,
    TriggerOutcome.WATCH: 1,
    TriggerOutcome.WARNING: 2,
    TriggerOutcome.EMERGENCY: 3,
}


@dataclass(frozen=True)
class TriggerResult:
    """One rule's verdict, with the arithmetic that produced it."""

    trigger_id: TriggerId
    outcome: TriggerOutcome
    detail: str
    observed: dict[str, Any] = field(default_factory=dict)
    threshold: dict[str, Any] = field(default_factory=dict)
    # Where the threshold came from: a vessel class, a named constant, a published polygon.
    threshold_source: str = ""
    evidence_age_seconds: float | None = None

    @property
    def fired(self) -> bool:
        return OUTCOME_RANK[self.outcome] >= OUTCOME_RANK[TriggerOutcome.WATCH]

    @property
    def rank(self) -> int:
        return OUTCOME_RANK[self.outcome]


def _unevaluated(trigger_id: TriggerId, detail: str) -> TriggerResult:
    return TriggerResult(trigger_id=trigger_id, outcome=TriggerOutcome.UNEVALUATED, detail=detail)


# --------------------------------------------------------------------------------------
# (a) Cyclone containment
# --------------------------------------------------------------------------------------


def check_cyclone_wind(
    cyclone: CycloneSystem, lat: float, lon: float, *, now: datetime
) -> TriggerResult:
    """Is the vessel inside a published wind-radius ring?

    The ring itself sets the severity: 64 kt is the eyewall of a severe system and nothing
    small survives it, 50 kt will overwhelm any open boat, and 34 kt is already beyond every
    class threshold ORCA models. 27 kt is a watch — bad, but a trawler can run for shelter.
    """
    ring = cyclone.strongest_ring_containing(lat, lon)
    age_seconds = cyclone.age(now).total_seconds()

    if ring is None:
        return TriggerResult(
            trigger_id=TriggerId.CYCLONE_WIND,
            outcome=TriggerOutcome.CLEAR,
            detail=f"outside every published wind radius of {cyclone.name}",
            observed={"system": cyclone.name, "inside_ring_knots": None},
            threshold_source=f"{cyclone.source.value} cyclone_wind polygons",
            evidence_age_seconds=age_seconds,
        )

    outcome = (
        TriggerOutcome.EMERGENCY
        if ring >= 50
        else TriggerOutcome.WARNING
        if ring >= 34
        else TriggerOutcome.WATCH
    )
    return TriggerResult(
        trigger_id=TriggerId.CYCLONE_WIND,
        outcome=outcome,
        detail=f"inside the {ring} kt wind radius of {cyclone.name}",
        observed={"system": cyclone.name, "inside_ring_knots": ring, "lat": lat, "lon": lon},
        threshold={"emergency_knots": 50, "warning_knots": 34, "watch_knots": 27},
        threshold_source=f"{cyclone.source.value} cyclone_wind polygons",
        evidence_age_seconds=age_seconds,
    )


def check_cyclone_cone(
    cyclone: CycloneSystem, lat: float, lon: float, *, now: datetime
) -> TriggerResult:
    """Is the vessel inside the forecast cone of uncertainty?

    A watch, not a warning, and deliberately so. The cone is the set of positions the storm
    *may* take, so a vessel inside it is not in the wind — it is somewhere the wind may
    arrive. Treating that as a warning would make every cone alert cry wolf, and the wind
    radius rule above is what escalates when the storm actually arrives.
    """
    if cyclone.cone_of_uncertainty is None:
        return _unevaluated(TriggerId.CYCLONE_CONE, f"no cone published for {cyclone.name}")

    inside = cyclone.in_cone(lat, lon)
    return TriggerResult(
        trigger_id=TriggerId.CYCLONE_CONE,
        outcome=TriggerOutcome.WATCH if inside else TriggerOutcome.CLEAR,
        detail=(
            f"inside the forecast cone of {cyclone.name}"
            if inside
            else f"outside the forecast cone of {cyclone.name}"
        ),
        observed={"system": cyclone.name, "in_cone": inside},
        threshold_source=f"{cyclone.source.value} cyclone_cou polygon",
        evidence_age_seconds=cyclone.age(now).total_seconds(),
    )


# --------------------------------------------------------------------------------------
# (b) Vessel-relative thresholds
# --------------------------------------------------------------------------------------


def check_wave_height(
    conditions: MarineConditions, vessel: VesselProfile, *, now: datetime
) -> TriggerResult:
    """Wave height against *this boat's* caution and avoid thresholds.

    The thresholds come from :data:`orca_kernels.CLASS_THRESHOLDS` — the same numbers the
    safety kernel scores against — so a proactive alert and an answered question can never
    disagree about whether conditions suit a boat.
    """
    if conditions.significant_wave_height_m is None:
        return _unevaluated(TriggerId.WAVE_HEIGHT, "no wave height in the forecast")
    age = conditions.age(now)
    if age > MAX_CONDITION_AGE:
        return _unevaluated(
            TriggerId.WAVE_HEIGHT,
            f"wave forecast is {age.total_seconds() / 3600:.1f} h old, too stale to alert on",
        )

    thresholds = vessel.thresholds
    value = conditions.significant_wave_height_m
    if value >= thresholds.avoid_wave_height_m:
        outcome = TriggerOutcome.WARNING
    elif value >= thresholds.caution_wave_height_m:
        outcome = TriggerOutcome.WATCH
    else:
        outcome = TriggerOutcome.CLEAR

    return TriggerResult(
        trigger_id=TriggerId.WAVE_HEIGHT,
        outcome=outcome,
        detail=(
            f"wave height {value:.1f} m against {thresholds.avoid_wave_height_m:.1f} m avoid "
            f"for a {vessel.vessel_class.value}"
        ),
        observed={"significant_wave_height_m": value},
        threshold={
            "caution_m": thresholds.caution_wave_height_m,
            "avoid_m": thresholds.avoid_wave_height_m,
        },
        threshold_source=f"CLASS_THRESHOLDS[{vessel.vessel_class.value}]",
        evidence_age_seconds=age.total_seconds(),
    )


def check_wind_speed(
    conditions: MarineConditions, vessel: VesselProfile, *, now: datetime
) -> TriggerResult:
    """Wind speed against this boat's class thresholds."""
    if conditions.wind_speed_ms is None:
        return _unevaluated(TriggerId.WIND_SPEED, "no wind speed in the forecast")
    age = conditions.age(now)
    if age > MAX_CONDITION_AGE:
        return _unevaluated(
            TriggerId.WIND_SPEED,
            f"wind forecast is {age.total_seconds() / 3600:.1f} h old, too stale to alert on",
        )

    thresholds = vessel.thresholds
    value = conditions.wind_speed_ms
    if value >= thresholds.avoid_wind_speed_ms:
        outcome = TriggerOutcome.WARNING
    elif value >= thresholds.caution_wind_speed_ms:
        outcome = TriggerOutcome.WATCH
    else:
        outcome = TriggerOutcome.CLEAR

    return TriggerResult(
        trigger_id=TriggerId.WIND_SPEED,
        outcome=outcome,
        detail=(
            f"wind {value:.1f} m/s against {thresholds.avoid_wind_speed_ms:.1f} m/s avoid "
            f"for a {vessel.vessel_class.value}"
        ),
        observed={"wind_speed_ms": value},
        threshold={
            "caution_ms": thresholds.caution_wind_speed_ms,
            "avoid_ms": thresholds.avoid_wind_speed_ms,
        },
        threshold_source=f"CLASS_THRESHOLDS[{vessel.vessel_class.value}]",
        evidence_age_seconds=age.total_seconds(),
    )


def check_lightning(
    conditions: MarineConditions, vessel: VesselProfile, *, now: datetime
) -> TriggerResult:
    """Cloud-to-ground strikes near the vessel.

    An open boat is the tallest object on a flat sea and has no bonded conductor to anywhere.
    A decked trawler with a steel mast is not safe either, but it is not the same exposure,
    so an undecked hull escalates one level at the same strike count.
    """
    if conditions.lightning_strikes_within_25km is None:
        return _unevaluated(TriggerId.LIGHTNING, "no lightning data for this position")

    strikes = conditions.lightning_strikes_within_25km
    if strikes >= LIGHTNING_WARNING_STRIKES:
        outcome = TriggerOutcome.WARNING
    elif strikes >= LIGHTNING_WATCH_STRIKES:
        outcome = TriggerOutcome.WATCH
    else:
        outcome = TriggerOutcome.CLEAR

    # An open hull has nowhere to shelter; the same storm is a level worse for it.
    if outcome is TriggerOutcome.WARNING and not _is_decked(vessel):
        outcome = TriggerOutcome.EMERGENCY

    return TriggerResult(
        trigger_id=TriggerId.LIGHTNING,
        outcome=outcome,
        detail=f"{strikes} strike(s) within 25 km",
        observed={"strikes_within_25km": strikes, "decked_hull": _is_decked(vessel)},
        threshold={"watch": LIGHTNING_WATCH_STRIKES, "warning": LIGHTNING_WARNING_STRIKES},
        threshold_source="orca_alerts.triggers.LIGHTNING_* (engineering judgement)",
        evidence_age_seconds=conditions.age(now).total_seconds(),
    )


def check_squall(
    conditions: MarineConditions, vessel: VesselProfile, *, now: datetime
) -> TriggerResult:
    """Nowcast squall probability.

    The bar is low because the failure mode is fast: a squall arrives in minutes and
    capsizes small craft before anything can be hauled. Waiting for certainty here means
    warning after the event.
    """
    if conditions.squall_probability is None:
        return _unevaluated(TriggerId.SQUALL, "no squall nowcast for this position")

    probability = conditions.squall_probability
    if probability >= SQUALL_WARNING_PROBABILITY:
        outcome = TriggerOutcome.WARNING
    elif probability >= SQUALL_WATCH_PROBABILITY:
        outcome = TriggerOutcome.WATCH
    else:
        outcome = TriggerOutcome.CLEAR

    return TriggerResult(
        trigger_id=TriggerId.SQUALL,
        outcome=outcome,
        detail=f"squall probability {probability:.0%}",
        observed={"squall_probability": probability},
        threshold={"watch": SQUALL_WATCH_PROBABILITY, "warning": SQUALL_WARNING_PROBABILITY},
        threshold_source="orca_alerts.triggers.SQUALL_* (engineering judgement)",
        evidence_age_seconds=conditions.age(now).total_seconds(),
    )


def _is_decked(vessel: VesselProfile) -> bool:
    """Whether the hull offers any shelter at all.

    Freeboard is the available proxy in the vessel profile. The 1.2 m line separates open
    vallams and catamarans from decked gillnetters and trawlers.
    """
    return vessel.freeboard_m >= 1.2


# --------------------------------------------------------------------------------------
# (c) Geofence proximity and predictive drift
# --------------------------------------------------------------------------------------


def check_geofence_proximity(proximity: BoundaryProximity) -> TriggerResult:
    """Distance to the IMBL, mapped from the Phase 2 warning band.

    A crossing is an emergency in the ordinary sense of the word: the 2025 Palk Strait
    arrests are what this system exists to prevent, and the consequence is detention, not
    discomfort.
    """
    outcome = {
        WarningBand.CLEAR: TriggerOutcome.CLEAR,
        WarningBand.AMBER: TriggerOutcome.WATCH,
        WarningBand.RED: TriggerOutcome.WARNING,
        WarningBand.CROSSED: TriggerOutcome.EMERGENCY,
    }[proximity.band]

    return TriggerResult(
        trigger_id=TriggerId.GEOFENCE_PROXIMITY,
        outcome=outcome,
        detail=(
            f"{proximity.distance_km:.1f} km from the India-Sri Lanka maritime boundary "
            f"({proximity.band.value})"
        ),
        observed={
            "distance_km": round(proximity.distance_km, 3),
            "band": proximity.band.value,
            "side": proximity.side.value,
            "bearing_to_boundary_deg": round(proximity.bearing_to_boundary_deg, 1),
        },
        threshold_source="orca_geo.BandThresholds over the agreed 1974/76 IMBL",
    )


def check_predictive_drift(forecast: DriftForecast) -> TriggerResult:
    """Time to boundary on the present course and current.

    The Tier-1 differentiator: warn *before* the crossing. Severity is set by how much time
    is left to act, not by distance — a boat 3 km off but closing at speed is in more
    trouble than one 1 km off and holding station.
    """
    if forecast.time_to_boundary is None:
        closest = forecast.closest_approach
        return TriggerResult(
            trigger_id=TriggerId.GEOFENCE_DRIFT,
            outcome=TriggerOutcome.CLEAR,
            detail=(
                "no crossing predicted within "
                f"{forecast.horizon.total_seconds() / 60:.0f} min on the present course"
            ),
            observed={"closest_approach_km": round(closest.distance_metres / 1000, 3)},
            threshold_source="orca_geo.DriftPredictor over the agreed 1974/76 IMBL",
        )

    remaining = forecast.time_to_boundary
    if remaining <= DRIFT_IMMEDIATE_HORIZON:
        outcome = TriggerOutcome.EMERGENCY
    elif remaining <= DRIFT_WARNING_HORIZON:
        outcome = TriggerOutcome.WARNING
    else:
        outcome = TriggerOutcome.WATCH

    minutes = remaining.total_seconds() / 60
    return TriggerResult(
        trigger_id=TriggerId.GEOFENCE_DRIFT,
        outcome=outcome,
        detail=f"predicted to reach the boundary in {minutes:.0f} min on the present course",
        observed={
            "time_to_boundary_minutes": round(minutes, 1),
            "crossing_lat": forecast.crossing_lat,
            "crossing_lon": forecast.crossing_lon,
        },
        threshold={
            "warning_minutes": DRIFT_WARNING_HORIZON.total_seconds() / 60,
            "emergency_minutes": DRIFT_IMMEDIATE_HORIZON.total_seconds() / 60,
        },
        threshold_source="orca_alerts.triggers.DRIFT_* (engineering judgement)",
    )


def evaluate_vessel_conditions(
    conditions: MarineConditions, vessel: VesselProfile, *, now: datetime
) -> tuple[TriggerResult, ...]:
    """Run every vessel-relative rule. Order is fixed so results are comparable run to run."""
    return (
        check_wave_height(conditions, vessel, now=now),
        check_wind_speed(conditions, vessel, now=now),
        check_lightning(conditions, vessel, now=now),
        check_squall(conditions, vessel, now=now),
    )


def evaluate_cyclones(
    picture: HazardPicture, lat: float, lon: float, *, now: datetime
) -> tuple[TriggerResult, ...]:
    """Run both cyclone rules against every active system."""
    results: list[TriggerResult] = []
    for cyclone in picture.active_cyclones():
        results.append(check_cyclone_wind(cyclone, lat, lon, now=now))
        results.append(check_cyclone_cone(cyclone, lat, lon, now=now))
    return tuple(results)


def worst(results: tuple[TriggerResult, ...]) -> TriggerResult | None:
    """The most severe fired rule, or ``None`` if nothing fired.

    Ties break on rule order rather than arbitrarily, so the same inputs always nominate the
    same rule as the reason for an alert — which is what makes deduplication stable.
    """
    fired = [r for r in results if r.fired]
    if not fired:
        return None
    return max(fired, key=lambda r: r.rank)


def subscriber_position(subscriber: Subscriber) -> tuple[float, float] | None:
    """The position to evaluate a subscriber at, if there is a usable one."""
    if subscriber.state is None:
        return None
    return (subscriber.state.lat, subscriber.state.lon)
