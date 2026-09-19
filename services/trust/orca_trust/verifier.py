"""The verifier: rules decide, critique explains (PLAN.md Phase 6.1, 6.3).

Composition order is the design:

1. run every deterministic rule;
2. derive the status from the rule outcomes alone — blocking failures abstain, warnings
   become caveats;
3. run the critique, which may add doubt and nothing else.

Step 2 completing before step 3 begins is what guarantees the verdict is reproducible.
The critique can change an answer into an abstention; it has no path back the other way.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from orca_schemas import BoundingBox, MarineVariable

from orca_trust.conflict import (
    ConflictResolution,
    SourceReading,
    check_source_disagreement,
    resolve_all,
)
from orca_trust.critique import Critic, CritiqueRequest, NullCritic, apply_critique
from orca_trust.rules import (
    EvidenceItem,
    check_evidence_sufficiency,
    check_formula_validity,
    check_freshness,
    check_missing_data,
    check_source_validity,
    check_spatial_consistency,
)
from orca_trust.verdict import (
    Caveat,
    CheckName,
    CheckResult,
    CheckSeverity,
    TrustVerdict,
    VerdictStatus,
)

# Formulas permitted to contribute to a decision. Registered explicitly so an unknown
# formula id is a blocking failure rather than a silent pass.
APPROVED_FORMULAS: frozenset[str] = frozenset(
    {
        "orca.safety.boat_relative_hazard",
        "orca.zones.pareto_frontier",
        "orca.routing.isochrone_astar",
        "orca.anomaly.threshold_flags",
    }
)


@dataclass(frozen=True)
class VerificationRequest:
    """Everything the verifier needs to reach a verdict."""

    evidence: Sequence[EvidenceItem]
    bbox: BoundingBox
    required_variables: frozenset[str]
    formula_ids: Sequence[str]
    readings_by_variable: dict[MarineVariable, Sequence[SourceReading]]
    evaluated_at: datetime


@dataclass(frozen=True)
class VerificationResult:
    """The verdict plus the conflict resolutions behind it."""

    verdict: TrustVerdict
    resolutions: tuple[ConflictResolution, ...]

    @property
    def abstained(self) -> bool:
        return self.verdict.abstained


class Verifier:
    """Deterministic gate, with an optional advisory critique."""

    def __init__(
        self,
        *,
        critic: Critic | None = None,
        approved_formulas: frozenset[str] = APPROVED_FORMULAS,
    ) -> None:
        self._critic = critic or NullCritic()
        self._approved = approved_formulas

    def verify(self, request: VerificationRequest) -> VerificationResult:
        """Run the rules, derive the verdict, then let the critique add doubt."""
        resolutions = resolve_all(request.readings_by_variable)

        checks: tuple[CheckResult, ...] = (
            check_source_validity(request.evidence),
            check_freshness(request.evidence, now=request.evaluated_at),
            check_spatial_consistency(request.evidence, bbox=request.bbox),
            check_missing_data(request.evidence, required_variables=request.required_variables),
            check_source_disagreement(resolutions),
            check_formula_validity(formula_ids=request.formula_ids, approved=self._approved),
            check_evidence_sufficiency(request.evidence),
        )

        verdict = self._compose(checks, request, resolutions)

        response = self._critic.review(
            CritiqueRequest(
                evidence_summary=tuple(
                    f"{i.provenance.source}:{i.variable or 'document'}"
                    f"@{i.provenance.issued_time.isoformat()}"
                    for i in request.evidence
                ),
                check_summary=tuple(
                    f"{c.name.value}={'pass' if c.passed else c.severity.value}" for c in checks
                ),
                proposed_status=verdict.status,
                evaluated_at=request.evaluated_at,
            )
        )
        return VerificationResult(
            verdict=apply_critique(verdict, response), resolutions=resolutions
        )

    def _compose(
        self,
        checks: tuple[CheckResult, ...],
        request: VerificationRequest,
        resolutions: tuple[ConflictResolution, ...],
    ) -> TrustVerdict:
        blocking = [c for c in checks if c.blocks]
        warnings = [c for c in checks if not c.passed and c.severity is CheckSeverity.WARNING]

        ages = [i.age(request.evaluated_at).total_seconds() for i in request.evidence]
        oldest = max(ages) if ages else None

        if blocking:
            return TrustVerdict(
                status=VerdictStatus.ABSTAIN,
                checks=checks,
                abstain_reasons=tuple(c.detail for c in blocking),
                remedy=_remedy_for(blocking),
                evidence_count=len(request.evidence),
                oldest_evidence_age_seconds=oldest,
                evaluated_at=request.evaluated_at,
            )

        caveats = tuple(r.as_caveat() for r in resolutions if r.in_conflict)
        caveats += tuple(
            Caveat(kind=c.name.value, detail=c.detail)
            for c in warnings
            if c.name is not CheckName.SOURCE_DISAGREEMENT
        )

        return TrustVerdict(
            status=VerdictStatus.ANSWER_WITH_CAVEATS if caveats else VerdictStatus.ANSWER,
            checks=checks,
            caveats=caveats,
            evidence_count=len(request.evidence),
            oldest_evidence_age_seconds=oldest,
            evaluated_at=request.evaluated_at,
        )


def _remedy_for(blocking: Sequence[CheckResult]) -> str:
    """What would have to change for an answer to become possible.

    An abstention that only says "no" is a dead end for the user; one that says what is
    missing tells them whether to wait ten minutes or give up.
    """
    remedies = {
        "freshness": "wait for the next forecast issue, or query a source with a shorter cadence",
        "missing_data": "obtain the missing variable(s) from another source",
        "evidence_sufficiency": "gather more corroborating evidence before deciding",
        "source_validity": "remove evidence from unrecognised sources",
        "source_disagreement": "wait for the sources to converge, or obtain in-situ observation",
        "formula_validity": "use an approved, versioned kernel",
    }
    return "; ".join(
        dict.fromkeys(remedies.get(c.name.value, "resolve the failed check") for c in blocking)
    )
