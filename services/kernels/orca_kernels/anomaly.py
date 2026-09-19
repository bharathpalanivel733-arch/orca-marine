"""Anomaly detection: HAB, marine heatwave, oil slick (PLAN.md Phase 4.5).

Threshold detectors over ocean-colour and SST anomalies, anchored to INCOIS's own
operational framing (the Algal Bloom Information Service, which detects *Noctiluca*
blooms in Indian waters).

These are **flags, not diagnoses**. A chlorophyll spike is consistent with a bloom and is
also consistent with a river plume, a sensor artefact or cloud-edge contamination. So
every detection states the evidence and its own confidence, and the wording downstream
must stay at "consistent with", never "there is". Getting this wrong in either direction
is costly: a missed bloom harms people, and a false one closes a fishery for nothing.

The marine-heatwave definition follows the standard one used in the literature — SST
above a local climatological percentile for five or more consecutive days — rather than
an invented threshold, because it is the definition a domain jury will expect.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from orca_kernels.contract import (
    AbstainReason,
    InputRole,
    KernelInput,
    KernelResult,
    abstain,
    evaluate_staleness,
)

KERNEL = "anomaly_detection"
FORMULA_ID = "orca.anomaly.threshold_flags"
FORMULA_VERSION = "1.0.0"
UNIT = "anomaly_flags"

# Chlorophyll above this multiple of the local baseline is consistent with a bloom.
HAB_CHLOROPHYLL_MULTIPLE = 3.0
HAB_ABSOLUTE_FLOOR_MG_M3 = 1.0

# Marine heatwave: the standard definition is SST above the local 90th percentile of the
# climatology for 5+ consecutive days.
HEATWAVE_MIN_DAYS = 5

# Ocean-colour signature of a surface slick: very low water-leaving radiance with
# suppressed chlorophyll retrieval. Weak on its own, hence the low confidence attached.
SLICK_MAX_CHLOROPHYLL_MG_M3 = 0.05


class AnomalyKind(StrEnum):
    HARMFUL_ALGAL_BLOOM = "harmful_algal_bloom"
    MARINE_HEATWAVE = "marine_heatwave"
    POSSIBLE_OIL_SLICK = "possible_oil_slick"


class AnomalyConfidence(StrEnum):
    """How much the evidence supports the flag."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


@dataclass(frozen=True)
class AnomalyFlag:
    """One detection, with the evidence that raised it."""

    kind: AnomalyKind
    confidence: AnomalyConfidence
    detail: str
    evidence: dict[str, float]

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "confidence": self.confidence.value,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def detect_harmful_algal_bloom(
    *, chlorophyll_mg_m3: float, baseline_mg_m3: float
) -> AnomalyFlag | None:
    """Flag chlorophyll far above the local baseline.

    Requires both a multiple of the baseline *and* an absolute floor: in very clear
    oligotrophic water, three times a tiny baseline is still a tiny number and not a
    bloom.
    """
    if baseline_mg_m3 <= 0:
        return None
    ratio = chlorophyll_mg_m3 / baseline_mg_m3
    if ratio < HAB_CHLOROPHYLL_MULTIPLE or chlorophyll_mg_m3 < HAB_ABSOLUTE_FLOOR_MG_M3:
        return None

    confidence = AnomalyConfidence.MODERATE if ratio < 5.0 else AnomalyConfidence.HIGH
    return AnomalyFlag(
        kind=AnomalyKind.HARMFUL_ALGAL_BLOOM,
        confidence=confidence,
        detail=(
            f"chlorophyll {chlorophyll_mg_m3:.2f} mg m-3 is {ratio:.1f}x the local baseline; "
            "consistent with an algal bloom. Ocean colour cannot identify the species — "
            "check INCOIS ABIS before treating this as Noctiluca."
        ),
        evidence={
            "chlorophyll_mg_m3": chlorophyll_mg_m3,
            "baseline_mg_m3": baseline_mg_m3,
            "ratio": round(ratio, 3),
        },
    )


def detect_marine_heatwave(
    *, daily_sst_c: Sequence[float], climatology_p90_c: float
) -> AnomalyFlag | None:
    """Flag five or more consecutive days above the local 90th-percentile SST."""
    if len(daily_sst_c) < HEATWAVE_MIN_DAYS:
        return None

    longest = current = 0
    for value in daily_sst_c:
        current = current + 1 if value > climatology_p90_c else 0
        longest = max(longest, current)

    if longest < HEATWAVE_MIN_DAYS:
        return None

    exceedance = max(daily_sst_c) - climatology_p90_c
    confidence = AnomalyConfidence.HIGH if longest >= 10 else AnomalyConfidence.MODERATE
    return AnomalyFlag(
        kind=AnomalyKind.MARINE_HEATWAVE,
        confidence=confidence,
        detail=(
            f"SST exceeded the local 90th percentile for {longest} consecutive days "
            f"(peak {exceedance:.2f} degC above it), meeting the standard marine-heatwave "
            "definition"
        ),
        evidence={
            "consecutive_days": float(longest),
            "peak_exceedance_c": round(exceedance, 3),
            "climatology_p90_c": climatology_p90_c,
        },
    )


def detect_oil_slick(*, chlorophyll_mg_m3: float, baseline_mg_m3: float) -> AnomalyFlag | None:
    """Flag suppressed ocean colour that can indicate a surface slick.

    Always low confidence. Ocean colour alone cannot distinguish a slick from sun glint
    or a calm patch; SAR is the instrument that can, and ORCA does not ingest it. The
    flag exists to prompt a look, not to assert a spill.
    """
    if baseline_mg_m3 <= 0 or chlorophyll_mg_m3 > SLICK_MAX_CHLOROPHYLL_MG_M3:
        return None
    if chlorophyll_mg_m3 >= baseline_mg_m3 * 0.2:
        return None

    return AnomalyFlag(
        kind=AnomalyKind.POSSIBLE_OIL_SLICK,
        confidence=AnomalyConfidence.LOW,
        detail=(
            f"chlorophyll retrieval {chlorophyll_mg_m3:.3f} mg m-3 is far below the local "
            f"baseline {baseline_mg_m3:.3f}; can indicate a surface slick, but sun glint and "
            "calm water look similar. Confirmation needs SAR, which ORCA does not ingest."
        ),
        evidence={
            "chlorophyll_mg_m3": chlorophyll_mg_m3,
            "baseline_mg_m3": baseline_mg_m3,
        },
    )


def detect_anomalies(
    *,
    evaluated_at: datetime,
    chlorophyll_mg_m3: float | None = None,
    chlorophyll_baseline_mg_m3: float | None = None,
    daily_sst_c: Sequence[float] | None = None,
    climatology_p90_c: float | None = None,
    inputs: tuple[KernelInput, ...] = (),
) -> KernelResult:
    """Run every detector whose inputs are present.

    Abstains only when nothing at all can be evaluated. An empty flag list is a real
    result meaning "nothing detected", which is different from "could not look".
    """
    declared = inputs or (
        KernelInput(
            name="anomaly_inputs",
            value=None,
            unit="mixed",
            source="evidence_layer",
            role=InputRole.OPTIONAL,
        ),
    )

    have_colour = chlorophyll_mg_m3 is not None and chlorophyll_baseline_mg_m3 is not None
    have_sst = daily_sst_c is not None and climatology_p90_c is not None

    if not have_colour and not have_sst:
        return abstain(
            kernel=KERNEL,
            formula_id=FORMULA_ID,
            formula_version=FORMULA_VERSION,
            unit=UNIT,
            inputs=declared,
            evaluated_at=evaluated_at,
            reason=AbstainReason.MISSING_REQUIRED_INPUT,
            detail=(
                "neither ocean-colour nor SST series was available, "
                "so nothing could be evaluated"
            ),
        )

    flags: list[AnomalyFlag] = []
    if have_colour:
        assert chlorophyll_mg_m3 is not None and chlorophyll_baseline_mg_m3 is not None
        bloom = detect_harmful_algal_bloom(
            chlorophyll_mg_m3=chlorophyll_mg_m3, baseline_mg_m3=chlorophyll_baseline_mg_m3
        )
        if bloom:
            flags.append(bloom)
        slick = detect_oil_slick(
            chlorophyll_mg_m3=chlorophyll_mg_m3, baseline_mg_m3=chlorophyll_baseline_mg_m3
        )
        if slick:
            flags.append(slick)

    if have_sst:
        assert daily_sst_c is not None and climatology_p90_c is not None
        heatwave = detect_marine_heatwave(
            daily_sst_c=daily_sst_c, climatology_p90_c=climatology_p90_c
        )
        if heatwave:
            flags.append(heatwave)

    return KernelResult(
        kernel=KERNEL,
        formula_id=FORMULA_ID,
        formula_version=FORMULA_VERSION,
        value=float(len(flags)),
        unit=UNIT,
        inputs=declared,
        staleness=evaluate_staleness(declared, evaluated_at=evaluated_at),
        extras={
            "flags": [flag.as_dict() for flag in flags],
            "evaluated": {
                "ocean_colour": have_colour,
                "sst_series": have_sst,
            },
        },
    )
