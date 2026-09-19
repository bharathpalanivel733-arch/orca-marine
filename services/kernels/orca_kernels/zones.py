"""Pareto fishing-zone intelligence (PLAN.md Phase 4.3, METHODS.md §1).

**Returns a trade-off frontier, never a single point.** A closer zone with a lower catch
probability and a further one with a better chance are both defensible choices, and which
is right depends on fuel in the tank, crew, and how the skipper feels about the weather —
things ORCA does not know. Collapsing that into one "best zone" would be inventing a
preference the fisherman never stated, and hiding the trade-off that makes the answer
useful.

Objectives ranked over, per METHODS.md §1:

* PFZ proximity and **advisory age** — PFZ is a probability-of-aggregation indicator
  issued three times a week, not a fish census, so an old advisory is weaker evidence
* SST-front strength and chlorophyll gradient — the oceanographic basis for aggregation
* distance and fuel cost
* the boat-relative safety score for that zone
* IMBL / MPA / seasonal-ban compliance

Compliance is **not an objective** — it is a hard filter. A zone across the IMBL or inside
a no-take MPA is removed before ranking, never offered as a cheaper option with a caveat.
Dominance is computed only over the objectives that remain.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from orca_kernels.contract import (
    AbstainReason,
    InputRole,
    KernelInput,
    KernelResult,
    abstain,
    evaluate_staleness,
)

KERNEL = "fishing_zone_intelligence"
FORMULA_ID = "orca.zones.pareto_frontier"
FORMULA_VERSION = "1.0.0"
UNIT = "zone_ranking"

# PFZ advisories are issued 3x/week (~56 h). Past twice that the advisory is describing a
# sea state two cycles old and its weight is floored.
PFZ_FULL_WEIGHT_AGE = timedelta(hours=56)
PFZ_ZERO_WEIGHT_AGE = timedelta(hours=112)


@dataclass(frozen=True)
class ZoneCandidate:
    """One candidate fishing ground, with its objectives already measured.

    Objectives are stored in the direction they are *better* in, which is what keeps the
    dominance test readable: every value here is "higher is better" except the costs,
    which are declared separately.
    """

    zone_id: str
    lat: float
    lon: float
    distance_nm: float
    fuel_cost_litres: float
    safety_score: float
    pfz_proximity_km: float | None = None
    pfz_issued_at: datetime | None = None
    sst_front_strength: float = 0.0
    chlorophyll_gradient: float = 0.0
    compliant: bool = True
    compliance_detail: str | None = None
    evidence: tuple[KernelInput, ...] = field(default_factory=tuple)

    def pfz_weight(self, now: datetime) -> float:
        """How much the PFZ signal counts, given its age.

        Returns 0 when there is no advisory at all, so a zone with no PFZ evidence is not
        credited with one.
        """
        if self.pfz_proximity_km is None or self.pfz_issued_at is None:
            return 0.0
        age = now - self.pfz_issued_at
        if age <= PFZ_FULL_WEIGHT_AGE:
            return 1.0
        if age >= PFZ_ZERO_WEIGHT_AGE:
            return 0.0
        span = (PFZ_ZERO_WEIGHT_AGE - PFZ_FULL_WEIGHT_AGE).total_seconds()
        return 1.0 - (age - PFZ_FULL_WEIGHT_AGE).total_seconds() / span

    def catch_signal(self, now: datetime) -> float:
        """Combined, age-weighted evidence that fish aggregate here.

        Deliberately crude and transparent: proximity to a PFZ decayed by its age, plus
        front strength and chlorophyll gradient. It is a ranking signal, not a catch
        prediction, and nothing downstream should present it as one.
        """
        weight = self.pfz_weight(now)
        proximity = 0.0
        if self.pfz_proximity_km is not None and weight > 0:
            # 1.0 at the advisory node, decaying to 0 by 50 km.
            proximity = max(0.0, 1.0 - self.pfz_proximity_km / 50.0) * weight
        return proximity + self.sst_front_strength + self.chlorophyll_gradient

    def objectives(self, now: datetime) -> tuple[float, ...]:
        """The vector dominance is computed over, all "higher is better"."""
        return (
            self.catch_signal(now),
            -self.distance_nm,
            -self.fuel_cost_litres,
            self.safety_score,
        )


def dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    """Whether ``a`` Pareto-dominates ``b``.

    At least as good on every objective, and strictly better on at least one.
    """
    if len(a) != len(b):
        msg = "objective vectors must have the same length"
        raise ValueError(msg)
    return all(x >= y for x, y in zip(a, b, strict=True)) and any(
        x > y for x, y in zip(a, b, strict=True)
    )


def pareto_frontier(
    candidates: Sequence[ZoneCandidate], *, now: datetime
) -> tuple[ZoneCandidate, ...]:
    """The non-dominated candidates, in deterministic order.

    Ordering is by catch signal then zone id, so the same inputs always produce the same
    frontier in the same order — the UI must not reshuffle between identical queries.
    """
    frontier = [
        candidate
        for candidate in candidates
        if not any(
            dominates(other.objectives(now), candidate.objectives(now))
            for other in candidates
            if other.zone_id != candidate.zone_id
        )
    ]
    return tuple(sorted(frontier, key=lambda c: (-c.catch_signal(now), c.zone_id)))


def rank_fishing_zones(
    *,
    candidates: Sequence[ZoneCandidate],
    evaluated_at: datetime,
    max_options: int = 3,
) -> KernelResult:
    """Return the trade-off frontier over compliant zones, or abstain.

    ``max_options`` caps what is shown: METHODS.md asks for a frontier, and a frontier of
    fifteen options is not a decision aid. The cap keeps the widest-spread options by
    taking the extremes of the frontier plus the best catch signal.
    """
    compliant = [c for c in candidates if c.compliant]
    excluded = [c for c in candidates if not c.compliant]

    inputs: tuple[KernelInput, ...] = tuple(
        item for candidate in candidates for item in candidate.evidence
    ) or (
        KernelInput(
            name="zone_candidates",
            value=float(len(candidates)),
            unit="count",
            source="zone_search",
            role=InputRole.REQUIRED,
        ),
    )

    if not candidates:
        return abstain(
            kernel=KERNEL,
            formula_id=FORMULA_ID,
            formula_version=FORMULA_VERSION,
            unit=UNIT,
            inputs=inputs,
            evaluated_at=evaluated_at,
            reason=AbstainReason.INSUFFICIENT_EVIDENCE,
            detail="no candidate zones were supplied",
        )

    if not compliant:
        reasons = "; ".join(
            f"{c.zone_id}: {c.compliance_detail or 'non-compliant'}" for c in excluded
        )
        return abstain(
            kernel=KERNEL,
            formula_id=FORMULA_ID,
            formula_version=FORMULA_VERSION,
            unit=UNIT,
            inputs=inputs,
            evaluated_at=evaluated_at,
            reason=AbstainReason.CONSTRAINT_VIOLATION,
            detail=f"every candidate zone is excluded by a hard constraint ({reasons})",
        )

    frontier = pareto_frontier(compliant, now=evaluated_at)
    selected = _spread(frontier, max_options=max_options, now=evaluated_at)

    return KernelResult(
        kernel=KERNEL,
        formula_id=FORMULA_ID,
        formula_version=FORMULA_VERSION,
        value=float(len(selected)),
        unit=UNIT,
        inputs=inputs,
        staleness=evaluate_staleness(inputs, evaluated_at=evaluated_at),
        extras={
            "frontier": [
                {
                    "zone_id": c.zone_id,
                    "lat": c.lat,
                    "lon": c.lon,
                    "catch_signal": round(c.catch_signal(evaluated_at), 4),
                    "distance_nm": c.distance_nm,
                    "fuel_cost_litres": c.fuel_cost_litres,
                    "safety_score": c.safety_score,
                    "pfz_weight": round(c.pfz_weight(evaluated_at), 4),
                }
                for c in selected
            ],
            "frontier_size": len(frontier),
            "excluded_by_constraint": [
                {"zone_id": c.zone_id, "reason": c.compliance_detail or "non-compliant"}
                for c in excluded
            ],
            "objectives": [
                "catch_signal",
                "distance_nm (lower better)",
                "fuel_cost_litres (lower better)",
                "safety_score",
            ],
        },
    )


def _spread(
    frontier: tuple[ZoneCandidate, ...], *, max_options: int, now: datetime
) -> tuple[ZoneCandidate, ...]:
    """Pick options that actually differ from one another.

    Taking the top N by catch signal would return three near-identical zones and hide the
    trade-off. This keeps the best catch, the safest and the cheapest, which is the
    choice a skipper is actually making.
    """
    if len(frontier) <= max_options:
        return frontier

    best_catch = max(frontier, key=lambda c: c.catch_signal(now))
    safest = max(frontier, key=lambda c: c.safety_score)
    cheapest = min(frontier, key=lambda c: c.fuel_cost_litres)

    chosen: list[ZoneCandidate] = []
    for candidate in (best_catch, safest, cheapest):
        if candidate not in chosen:
            chosen.append(candidate)

    for candidate in frontier:
        if len(chosen) >= max_options:
            break
        if candidate not in chosen:
            chosen.append(candidate)

    return tuple(sorted(chosen[:max_options], key=lambda c: (-c.catch_signal(now), c.zone_id)))
