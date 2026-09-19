"""Variable-specific source-conflict resolution (PLAN.md Phase 6.2, gap M10).

When INCOIS and CMEMS disagree about significant wave height by more than half a metre,
three things must happen — and the order matters:

1. **Prefer the more reliable source** for that (region, variable, lead-time). Reliability
   is the backtested skill from Phase 10.1; until that exists it is the registry's
   declared prior, and it is labelled as a prior wherever it surfaces.
2. **Widen the uncertainty interval** to span both readings. This is the step most systems
   skip, and skipping it is what makes a disagreement disappear: picking the better source
   and reporting its narrow band tells the user the sea state is known to ±0.2 m when two
   credible sources differ by 0.9 m.
3. **Disclose the conflict.** It travels to the user as a caveat, not into a log.

Averaging is deliberately **not** an option. The mean of a 1.2 m and a 2.4 m forecast is a
number neither source predicted, it belongs to no provenance, and it cannot be replayed
from either payload.

Thresholds are per-variable because the variables are not comparable: half a metre of wave
height is the difference between routine and dangerous for an FRP vallam, whereas half a
degree of SST means almost nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from orca_schemas import MarineVariable

from orca_trust.verdict import Caveat, CheckName, CheckResult, CheckSeverity

# Disagreement beyond this, in the variable's canonical unit, is a real conflict rather
# than model noise. Values are engineering judgements, not sourced constants, and each is
# set at roughly the point where the difference would change a decision.
CONFLICT_THRESHOLDS: dict[MarineVariable, float] = {
    MarineVariable.SIGNIFICANT_WAVE_HEIGHT: 0.5,  # m — METHODS.md §1 example
    MarineVariable.SWELL_HEIGHT: 0.5,  # m
    MarineVariable.PEAK_WAVE_PERIOD: 2.0,  # s
    MarineVariable.MEAN_WAVE_DIRECTION: 45.0,  # degrees
    MarineVariable.WIND_SPEED: 3.0,  # m s-1
    MarineVariable.WIND_DIRECTION: 45.0,  # degrees
    MarineVariable.SEA_SURFACE_TEMPERATURE: 1.5,  # degC
    MarineVariable.CHLOROPHYLL: 1.0,  # mg m-3
    MarineVariable.CURRENT_SPEED: 0.5,  # m s-1
    MarineVariable.CURRENT_DIRECTION: 45.0,  # degrees
    MarineVariable.SEA_SURFACE_SALINITY: 1.0,
    MarineVariable.MIXED_LAYER_DEPTH: 20.0,  # m
    MarineVariable.DEPTH_OF_20C_ISOTHERM: 20.0,  # m
    MarineVariable.SEA_LEVEL: 0.3,  # m
}

# Beyond this multiple of the threshold the sources are not merely disagreeing, they are
# describing different seas. Preferring one would be arbitrary, so the layer abstains.
IRRECONCILABLE_MULTIPLE = 4.0


@dataclass(frozen=True)
class SourceReading:
    """One source's value for a variable, with the reliability behind it."""

    source: str
    variable: MarineVariable
    value: float
    unit: str
    issued_time: datetime
    reliability: float
    provenance_id: str
    lead_time_hours: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.reliability <= 1.0:
            msg = f"reliability must be in [0, 1], got {self.reliability}"
            raise ValueError(msg)


@dataclass(frozen=True)
class ConflictResolution:
    """What the layer decided about one disagreement."""

    variable: MarineVariable
    chosen: SourceReading
    rejected: tuple[SourceReading, ...]
    spread: float
    threshold: float
    lower: float
    upper: float
    irreconcilable: bool
    detail: str

    @property
    def in_conflict(self) -> bool:
        return bool(self.rejected) and self.spread > self.threshold

    @property
    def interval_width(self) -> float:
        return self.upper - self.lower

    def as_caveat(self) -> Caveat:
        """The disclosure the user sees."""
        return Caveat(
            kind="source_disagreement",
            detail=self.detail,
            widened_uncertainty=True,
        )


def threshold_for(variable: MarineVariable) -> float:
    """The disagreement threshold for a variable, in its canonical unit."""
    try:
        return CONFLICT_THRESHOLDS[variable]
    except KeyError:  # pragma: no cover - every MarineVariable has an entry
        msg = f"no conflict threshold defined for {variable}"
        raise KeyError(msg) from None


def resolve_conflict(readings: Sequence[SourceReading]) -> ConflictResolution:
    """Resolve disagreement between sources for one variable.

    With a single reading there is nothing to resolve and the interval is that reading.
    With several, the most reliable wins, the interval spans all of them, and the result
    carries the disclosure text.
    """
    if not readings:
        msg = "cannot resolve a conflict with no readings"
        raise ValueError(msg)

    variable = readings[0].variable
    if any(r.variable is not variable for r in readings):
        msg = "all readings must be for the same variable"
        raise ValueError(msg)

    threshold = threshold_for(variable)
    values = [r.value for r in readings]
    spread = max(values) - min(values)

    # Deterministic ordering: reliability first, then source id so ties never flip
    # between runs. A non-deterministic winner would break replay.
    ranked = sorted(readings, key=lambda r: (-r.reliability, r.source))
    chosen = ranked[0]
    rejected = tuple(ranked[1:])

    if len(readings) == 1 or spread <= threshold:
        return ConflictResolution(
            variable=variable,
            chosen=chosen,
            rejected=rejected,
            spread=spread,
            threshold=threshold,
            lower=min(values),
            upper=max(values),
            irreconcilable=False,
            detail=(
                f"{len(readings)} source(s) agree on {variable} within {threshold} "
                f"{chosen.unit} (spread {spread:.2f})"
            ),
        )

    irreconcilable = spread > threshold * IRRECONCILABLE_MULTIPLE
    others = ", ".join(f"{r.source} {r.value:.2f}" for r in rejected)
    detail = (
        f"sources disagree on {variable}: {chosen.source} reports {chosen.value:.2f} "
        f"{chosen.unit} (reliability {chosen.reliability:.2f}), {others}. "
        f"Spread {spread:.2f} exceeds the {threshold} {chosen.unit} threshold, so the more "
        f"reliable source is used and the uncertainty is widened to span all readings."
    )
    if irreconcilable:
        detail = (
            f"sources disagree irreconcilably on {variable}: spread {spread:.2f} "
            f"{chosen.unit} is more than {IRRECONCILABLE_MULTIPLE:g}x the {threshold} "
            f"threshold ({chosen.source} {chosen.value:.2f} vs {others}). "
            "Preferring either would be arbitrary."
        )

    return ConflictResolution(
        variable=variable,
        chosen=chosen,
        rejected=rejected,
        spread=spread,
        threshold=threshold,
        # The interval spans every reading: the disagreement IS the uncertainty.
        lower=min(values),
        upper=max(values),
        irreconcilable=irreconcilable,
        detail=detail,
    )


def resolve_all(
    readings_by_variable: dict[MarineVariable, Sequence[SourceReading]],
) -> tuple[ConflictResolution, ...]:
    """Resolve every variable, in a deterministic order."""
    return tuple(
        resolve_conflict(readings_by_variable[variable])
        for variable in sorted(readings_by_variable, key=lambda v: v.value)
    )


def check_source_disagreement(
    resolutions: Sequence[ConflictResolution],
) -> CheckResult:
    """Turn conflict resolutions into a verifier check.

    A resolved conflict is a **warning**: the answer stands with a widened interval and a
    disclosure. An irreconcilable one is **blocking**: no defensible choice exists.
    """
    conflicts = [r for r in resolutions if r.in_conflict]
    irreconcilable = [r for r in conflicts if r.irreconcilable]

    if irreconcilable:
        worst = max(irreconcilable, key=lambda r: r.spread)
        return CheckResult(
            name=CheckName.SOURCE_DISAGREEMENT,
            passed=False,
            severity=CheckSeverity.BLOCKING,
            detail=worst.detail,
            observed={
                "irreconcilable": [
                    {"variable": r.variable.value, "spread": round(r.spread, 3)}
                    for r in irreconcilable
                ]
            },
            threshold={"irreconcilable_multiple": IRRECONCILABLE_MULTIPLE},
        )

    if conflicts:
        return CheckResult(
            name=CheckName.SOURCE_DISAGREEMENT,
            passed=False,
            severity=CheckSeverity.WARNING,
            detail="; ".join(r.detail for r in conflicts),
            observed={
                "conflicts": [
                    {
                        "variable": r.variable.value,
                        "spread": round(r.spread, 3),
                        "chosen": r.chosen.source,
                        "interval": [round(r.lower, 3), round(r.upper, 3)],
                    }
                    for r in conflicts
                ]
            },
        )

    return CheckResult(
        name=CheckName.SOURCE_DISAGREEMENT,
        passed=True,
        severity=CheckSeverity.INFO,
        detail=f"no source disagreement beyond threshold across {len(resolutions)} variable(s)",
    )
