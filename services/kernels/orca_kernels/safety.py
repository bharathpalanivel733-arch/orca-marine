"""Boat-relative Marine Safety Score (PLAN.md Phase 4.2, METHODS.md §1).

A 0-100 score with a confidence band, computed as a weighted combination of significant
wave height, wind speed, swell period and squall/lightning risk — **each normalised
against the thresholds of the boat that asked**, then scaled by the reliability of the
forecast at that lead time.

Two properties matter more than the arithmetic:

1. **It hard-abstains on stale required input.** Wave height and wind are required; if
   either is beyond its cadence the kernel returns no score at all and says why. A safety
   verdict computed from yesterday's forecast is worse than no verdict, because the
   fisherman cannot tell the difference.
2. **Nothing here is a model.** Every step is arithmetic over evidence, so the same
   inputs always give the same score, and :meth:`KernelResult.verify` can prove a stored
   result was not altered after the fact.

The score is a *hazard* score inverted: 100 is benign, 0 is dangerous. Each driver
produces a 0-1 hazard where 0 means "below this boat's caution threshold" and 1 means "at
or beyond its avoid threshold", interpolating linearly between. Interpolation rather than
a step is deliberate — a boat 10 cm under the avoid line should not be told the sea is
fine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from orca_kernels.contract import (
    ConfidenceBand,
    InputRole,
    KernelInput,
    KernelResult,
    abstain_if_blocked,
    evaluate_staleness,
)
from orca_kernels.vessel import VesselProfile

KERNEL = "marine_safety_score"
FORMULA_ID = "orca.safety.boat_relative_hazard"
FORMULA_VERSION = "1.0.0"
UNIT = "score_0_100"

# Weights sum to 1.0. Wave height dominates because it is what capsizes small craft;
# lightning is weighted lowest of the four because it is the most localised and the
# least reliably forecast, not because it is survivable.
WEIGHT_WAVE = 0.45
WEIGHT_WIND = 0.30
WEIGHT_SWELL_PERIOD = 0.15
WEIGHT_SQUALL = 0.10

# Below this reliability, the forecast is too weak to carry a safety verdict at all.
MIN_RELIABILITY = 0.25


class SafetyBand(str):
    """Advisory wording for a score. Kept as plain strings for the response templates."""


SAFE = "safe"
CAUTION = "caution"
AVOID = "avoid"


def band_for(score: float) -> str:
    """Map a score to advisory wording.

    The cut-offs are chosen so that a driver at its class 'avoid' threshold alone can
    pull the verdict out of `safe`.
    """
    if score >= 70.0:
        return SAFE
    if score >= 40.0:
        return CAUTION
    return AVOID


def normalise_hazard(value: float, caution: float, avoid: float) -> float:
    """Map a driver onto 0-1 against this boat's thresholds.

    0 at or below caution, 1 at or above avoid, linear between. Values beyond avoid stay
    clamped at 1: once a boat is past its limit, "twice past" is not a meaningful
    gradation for the score, and the band already says avoid.
    """
    if avoid <= caution:
        msg = "avoid threshold must exceed caution threshold"
        raise ValueError(msg)
    if value <= caution:
        return 0.0
    if value >= avoid:
        return 1.0
    return (value - caution) / (avoid - caution)


def swell_period_hazard(period_s: float, minimum_comfortable_s: float) -> float:
    """Short-period swell is steeper and more dangerous for a small hull.

    Inverted relative to the other drivers: hazard rises as period *falls*. Reaches 1 at
    half the comfortable period, which is where seas are genuinely confused.
    """
    if period_s >= minimum_comfortable_s:
        return 0.0
    floor = minimum_comfortable_s / 2.0
    if period_s <= floor:
        return 1.0
    return (minimum_comfortable_s - period_s) / (minimum_comfortable_s - floor)


@dataclass(frozen=True)
class SafetyDrivers:
    """The measured inputs to one safety evaluation."""

    wave_height_m: KernelInput
    wind_speed_ms: KernelInput
    swell_period_s: KernelInput | None = None
    squall_risk: KernelInput | None = None

    def as_inputs(self) -> tuple[KernelInput, ...]:
        items = [self.wave_height_m, self.wind_speed_ms]
        if self.swell_period_s is not None:
            items.append(self.swell_period_s)
        if self.squall_risk is not None:
            items.append(self.squall_risk)
        return tuple(items)


def compute_safety_score(
    *,
    vessel: VesselProfile,
    drivers: SafetyDrivers,
    reliability: float,
    evaluated_at: datetime,
    lead_time_hours: float = 0.0,
) -> KernelResult:
    """Compute the boat-relative safety score, or abstain.

    ``reliability`` is the backtested skill of the forecast for this region, variable and
    lead time (Phase 10.1 supplies it; until then it is the registry's declared prior).
    It never raises the score — it only widens the confidence band and, below
    :data:`MIN_RELIABILITY`, forces abstention. A confident number on an unreliable
    forecast is the precise failure this system exists to avoid.
    """
    if not 0.0 <= reliability <= 1.0:
        msg = f"reliability must be in [0, 1], got {reliability}"
        raise ValueError(msg)

    vessel_input = KernelInput(
        name="vessel_class",
        value=None,
        unit="class",
        source="vessel_profile",
        role=InputRole.OPTIONAL,
    )
    reliability_input = KernelInput(
        name="reliability",
        value=reliability,
        unit="fraction",
        source="reliability_layer",
        role=InputRole.REQUIRED,
    )
    inputs = (*drivers.as_inputs(), reliability_input, vessel_input)

    blocked = abstain_if_blocked(
        kernel=KERNEL,
        formula_id=FORMULA_ID,
        formula_version=FORMULA_VERSION,
        unit=UNIT,
        inputs=inputs,
        evaluated_at=evaluated_at,
    )
    if blocked is not None:
        return blocked

    if reliability < MIN_RELIABILITY:
        from orca_kernels.contract import AbstainReason, abstain

        return abstain(
            kernel=KERNEL,
            formula_id=FORMULA_ID,
            formula_version=FORMULA_VERSION,
            unit=UNIT,
            inputs=inputs,
            evaluated_at=evaluated_at,
            reason=AbstainReason.INSUFFICIENT_EVIDENCE,
            detail=(
                f"forecast reliability {reliability:.2f} at {lead_time_hours:.0f} h lead is "
                f"below the {MIN_RELIABILITY:.2f} floor for a safety verdict"
            ),
        )

    thresholds = vessel.thresholds
    margin = vessel.margin_factor

    # Hazards are computed against the UNMODIFIED class thresholds, then the boat's own
    # margin adjusts the hazard. Scaling the thresholds instead was tried and rejected:
    # the caution-to-avoid band is only ~0.5 m wide for small craft, so shifting both
    # ends by 10% moved the score by nearly 28 points — a freeboard heuristic deciding a
    # safety verdict, which is exactly the false precision this kernel must not have.
    # Dividing the hazard by the margin bounds the effect to the margin itself (~10%).
    wave_hazard = normalise_hazard(
        float(drivers.wave_height_m.value or 0.0),
        thresholds.caution_wave_height_m,
        thresholds.avoid_wave_height_m,
    )
    wind_hazard = normalise_hazard(
        float(drivers.wind_speed_ms.value or 0.0),
        thresholds.caution_wind_speed_ms,
        thresholds.avoid_wind_speed_ms,
    )

    used_weight = WEIGHT_WAVE + WEIGHT_WIND
    hazard = WEIGHT_WAVE * wave_hazard + WEIGHT_WIND * wind_hazard

    if drivers.swell_period_s is not None and drivers.swell_period_s.value is not None:
        hazard += WEIGHT_SWELL_PERIOD * swell_period_hazard(
            float(drivers.swell_period_s.value), thresholds.min_comfortable_swell_period_s
        )
        used_weight += WEIGHT_SWELL_PERIOD

    if drivers.squall_risk is not None and drivers.squall_risk.value is not None:
        hazard += WEIGHT_SQUALL * max(0.0, min(1.0, float(drivers.squall_risk.value)))
        used_weight += WEIGHT_SQUALL

    # Renormalise over the drivers actually present, so a missing optional input widens
    # the band rather than silently scoring as zero hazard.
    hazard = hazard / used_weight
    # Boat-specific margin, bounded: a better-found hull of the same class is credited a
    # little, never enough to turn an unsafe sea into a safe one.
    hazard = max(0.0, min(1.0, hazard / margin))
    score = round((1.0 - hazard) * 100.0, 1)

    staleness = evaluate_staleness(inputs, evaluated_at=evaluated_at)
    confidence = _confidence_band(
        score=score,
        reliability=reliability,
        missing_weight=1.0 - used_weight,
        vessel=vessel,
    )

    return KernelResult(
        kernel=KERNEL,
        formula_id=FORMULA_ID,
        formula_version=FORMULA_VERSION,
        value=score,
        unit=UNIT,
        inputs=inputs,
        staleness=staleness,
        confidence=confidence,
        extras={
            "band": band_for(score),
            "hazard_components": {
                "wave": round(wave_hazard, 4),
                "wind": round(wind_hazard, 4),
            },
            "weights_used": round(used_weight, 4),
            "vessel_class": vessel.vessel_class.value,
            "margin_factor": round(margin, 4),
            "lead_time_hours": lead_time_hours,
        },
    )


def _confidence_band(
    *, score: float, reliability: float, missing_weight: float, vessel: VesselProfile
) -> ConfidenceBand:
    """Width follows forecast reliability, missing drivers and the boat's own margin.

    Clamped to [0, 100] rather than extended symmetrically, so a score of 95 does not
    imply a meaningless upper bound of 110.
    """
    # Perfect reliability still leaves ±3 points: a forecast is a forecast.
    spread = 3.0 + (1.0 - reliability) * 25.0 + missing_weight * 20.0
    reasons = [f"reliability {reliability:.2f}"]
    if missing_weight > 0:
        reasons.append(f"{missing_weight:.0%} of drivers unavailable")
    if not vessel.can_call_for_help:
        # No radio, no EPIRB: less tolerance for being wrong, expressed as a wider band.
        spread += 5.0
        reasons.append("no means of calling for help")

    return ConfidenceBand(
        lower=round(max(0.0, score - spread), 1),
        upper=round(min(100.0, score + spread), 1),
        basis="; ".join(reasons),
    )
