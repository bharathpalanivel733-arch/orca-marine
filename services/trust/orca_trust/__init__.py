"""ORCA trust layer (PLAN.md Phase 6).

Deterministic rules decide whether ORCA answers; an independent critique may only add
doubt. Source conflicts are resolved by reliability, with the uncertainty widened to span
the disagreement and the conflict disclosed rather than averaged away. Every decision is
persisted as a provenance graph — dataset, raw-payload hash, agent, formula version,
output — that a `run_id` can replay byte-for-byte from the archived evidence.

This is the concrete design that replaces the "7-point integrity check" and "Adversarial
Reliability Nucleus" language (METHODS.md section 2, PLAN.md 6.5): named checks with
stated thresholds, and provenance with evidence-sufficiency gating.
"""

from orca_trust.conflict import (
    CONFLICT_THRESHOLDS,
    IRRECONCILABLE_MULTIPLE,
    ConflictResolution,
    SourceReading,
    check_source_disagreement,
    resolve_all,
    resolve_conflict,
    threshold_for,
)
from orca_trust.critique import (
    Critic,
    CritiqueRequest,
    CritiqueResponse,
    LlmCritic,
    NullCritic,
    apply_critique,
)
from orca_trust.provenance import (
    ProvenanceBuilder,
    ProvenanceEdge,
    ProvenanceGraph,
    ProvenanceNode,
    ProvenanceNodeKind,
)
from orca_trust.replay import (
    PayloadArchiveReader,
    ReplayMismatch,
    ReplayOutcome,
    ReplayReport,
    replay_run,
    verify_payloads,
)
from orca_trust.rules import (
    ABSOLUTE_FRESHNESS_LIMIT,
    KNOWN_SOURCES,
    MIN_EVIDENCE_ITEMS,
    EvidenceItem,
    check_evidence_sufficiency,
    check_formula_validity,
    check_freshness,
    check_missing_data,
    check_source_validity,
    check_spatial_consistency,
)
from orca_trust.storage import ProvenanceStore
from orca_trust.verdict import (
    Caveat,
    CheckName,
    CheckResult,
    CheckSeverity,
    TrustVerdict,
    VerdictStatus,
)
from orca_trust.verifier import (
    APPROVED_FORMULAS,
    VerificationRequest,
    VerificationResult,
    Verifier,
)

SERVICE_NAME = "orca-trust"
__version__ = "0.1.0"

__all__ = [
    "ABSOLUTE_FRESHNESS_LIMIT",
    "APPROVED_FORMULAS",
    "CONFLICT_THRESHOLDS",
    "IRRECONCILABLE_MULTIPLE",
    "KNOWN_SOURCES",
    "MIN_EVIDENCE_ITEMS",
    "SERVICE_NAME",
    "Caveat",
    "CheckName",
    "CheckResult",
    "CheckSeverity",
    "ConflictResolution",
    "Critic",
    "CritiqueRequest",
    "CritiqueResponse",
    "EvidenceItem",
    "LlmCritic",
    "NullCritic",
    "PayloadArchiveReader",
    "ProvenanceBuilder",
    "ProvenanceEdge",
    "ProvenanceGraph",
    "ProvenanceNode",
    "ProvenanceNodeKind",
    "ProvenanceStore",
    "ReplayMismatch",
    "ReplayOutcome",
    "ReplayReport",
    "SourceReading",
    "TrustVerdict",
    "VerdictStatus",
    "VerificationRequest",
    "VerificationResult",
    "Verifier",
    "__version__",
    "apply_critique",
    "check_evidence_sufficiency",
    "check_formula_validity",
    "check_freshness",
    "check_missing_data",
    "check_source_disagreement",
    "check_source_validity",
    "check_spatial_consistency",
    "replay_run",
    "resolve_all",
    "resolve_conflict",
    "threshold_for",
    "verify_payloads",
]
