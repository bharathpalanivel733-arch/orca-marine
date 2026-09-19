"""ORCA deterministic decision kernels (PLAN.md Phase 4).

Every number a fisherman sees is computed here, by arithmetic over evidence records.
**No language model calculates, adjusts or invents any value in this package.** An LLM
may read a `KernelResult` and narrate it; the recorded formula id, version and inputs are
what make that separation checkable rather than merely asserted — see
`KernelResult.fingerprint`.

Kernels: boat-relative safety score, Pareto fishing zones, isochrone-A* routing, and
anomaly flags. All of them return the same `KernelResult` contract and all of them abstain
rather than guess when a required input is stale or missing.
"""

from orca_kernels.anomaly import (
    AnomalyConfidence,
    AnomalyFlag,
    AnomalyKind,
    detect_anomalies,
    detect_harmful_algal_bloom,
    detect_marine_heatwave,
    detect_oil_slick,
)
from orca_kernels.contract import (
    AbstainReason,
    ConfidenceBand,
    InputRole,
    KernelInput,
    KernelResult,
    Staleness,
    abstain,
    abstain_if_blocked,
    evaluate_staleness,
)
from orca_kernels.routing import (
    CostSurface,
    GridCell,
    RouteLeg,
    great_circle_baseline,
    plan_route,
)
from orca_kernels.safety import (
    SafetyDrivers,
    band_for,
    compute_safety_score,
    normalise_hazard,
    swell_period_hazard,
)
from orca_kernels.vessel import (
    CLASS_THRESHOLDS,
    ClassThresholds,
    SafetyEquipment,
    VesselClass,
    VesselProfile,
)
from orca_kernels.zones import (
    ZoneCandidate,
    dominates,
    pareto_frontier,
    rank_fishing_zones,
)

SERVICE_NAME = "orca-kernels"
__version__ = "0.1.0"

__all__ = [
    "CLASS_THRESHOLDS",
    "SERVICE_NAME",
    "AbstainReason",
    "AnomalyConfidence",
    "AnomalyFlag",
    "AnomalyKind",
    "ClassThresholds",
    "ConfidenceBand",
    "CostSurface",
    "GridCell",
    "InputRole",
    "KernelInput",
    "KernelResult",
    "RouteLeg",
    "SafetyDrivers",
    "SafetyEquipment",
    "Staleness",
    "VesselClass",
    "VesselProfile",
    "ZoneCandidate",
    "__version__",
    "abstain",
    "abstain_if_blocked",
    "band_for",
    "compute_safety_score",
    "detect_anomalies",
    "detect_harmful_algal_bloom",
    "detect_marine_heatwave",
    "detect_oil_slick",
    "dominates",
    "evaluate_staleness",
    "great_circle_baseline",
    "normalise_hazard",
    "pareto_frontier",
    "plan_route",
    "rank_fishing_zones",
    "swell_period_hazard",
]
