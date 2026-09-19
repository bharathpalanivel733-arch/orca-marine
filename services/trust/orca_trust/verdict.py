"""The typed trust verdict, with abstention as a first-class response (PLAN.md 6.3).

An abstention is **not an error**. `POST /decision` returning "I cannot safely answer,
the nearest reliable data is 6 h old" is a successful, well-formed response with a
different shape — it carries reasons, the evidence that was available, and what would
have to change. Modelling it as an exception would push it into a 5xx path where clients
treat it as a fault and retry, which is precisely the wrong behaviour for a refusal that
is deliberate.

For a life-safety, government-facing system calibrated refusal is a feature
(METHODS.md §2), and the demo rehearses it on purpose.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Self

from orca_schemas import OrcaModel
from pydantic import Field, field_validator, model_validator


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must be timezone-aware"
        raise ValueError(msg)
    return value


class VerdictStatus(StrEnum):
    """What the trust layer concluded.

    ``ANSWER_WITH_CAVEATS`` is deliberately distinct from ``ANSWER``: a result that
    survived a source conflict or a degraded input is answerable, but the caveat must
    reach the user rather than being averaged away.
    """

    ANSWER = "answer"
    ANSWER_WITH_CAVEATS = "answer_with_caveats"
    ABSTAIN = "abstain"


class CheckSeverity(StrEnum):
    """How a failed check affects the verdict."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class CheckName(StrEnum):
    """The deterministic checks the verifier runs.

    These replace the old "7-point integrity check" framing (PLAN.md 6.5): each is a
    named, testable rule with a stated threshold rather than a count of unspecified
    checks.
    """

    SOURCE_VALIDITY = "source_validity"
    FRESHNESS = "freshness"
    SPATIAL_CONSISTENCY = "spatial_consistency"
    MISSING_DATA = "missing_data"
    SOURCE_DISAGREEMENT = "source_disagreement"
    FORMULA_VALIDITY = "formula_validity"
    EVIDENCE_SUFFICIENCY = "evidence_sufficiency"


class CheckResult(OrcaModel):
    """One deterministic check, its verdict, and the numbers behind it."""

    name: CheckName
    passed: bool
    severity: CheckSeverity
    detail: str = Field(min_length=1)
    observed: dict[str, Any] = Field(
        default_factory=dict, description="The measured values the rule compared."
    )
    threshold: dict[str, Any] = Field(
        default_factory=dict, description="The threshold it compared them against."
    )

    @model_validator(mode="after")
    def _passed_checks_are_not_blocking(self) -> Self:
        if self.passed and self.severity is CheckSeverity.BLOCKING:
            msg = "a passing check cannot be blocking"
            raise ValueError(msg)
        return self

    @property
    def blocks(self) -> bool:
        return not self.passed and self.severity is CheckSeverity.BLOCKING


class Caveat(OrcaModel):
    """Something the user must be told even though an answer was produced."""

    kind: str
    detail: str
    widened_uncertainty: bool = False


class TrustVerdict(OrcaModel):
    """The trust layer's typed response.

    Carries the full check list whether it answers or abstains — the checks are the
    explanation, and a user who is refused deserves the same evidence as one who is not.
    """

    status: VerdictStatus
    checks: tuple[CheckResult, ...]
    caveats: tuple[Caveat, ...] = ()
    abstain_reasons: tuple[str, ...] = ()
    remedy: str | None = Field(
        default=None, description="What would have to change for an answer to be possible."
    )
    evidence_count: int = Field(default=0, ge=0)
    oldest_evidence_age_seconds: float | None = Field(default=None, ge=0)
    evaluated_at: datetime
    critique: str | None = Field(
        default=None,
        description="Explanatory critique text. Never the decision authority (PLAN.md 6.1).",
    )

    _aware = field_validator("evaluated_at")(_require_aware)

    @model_validator(mode="after")
    def _status_matches_the_checks(self) -> Self:
        blocking = [c for c in self.checks if c.blocks]
        if blocking and self.status is not VerdictStatus.ABSTAIN:
            names = ", ".join(c.name.value for c in blocking)
            msg = f"blocking check(s) present ({names}) but status is {self.status}"
            raise ValueError(msg)
        if self.status is VerdictStatus.ABSTAIN and not self.abstain_reasons:
            msg = "an abstention must carry at least one reason"
            raise ValueError(msg)
        if self.status is VerdictStatus.ANSWER_WITH_CAVEATS and not self.caveats:
            msg = "answer_with_caveats requires at least one caveat"
            raise ValueError(msg)
        return self

    @property
    def abstained(self) -> bool:
        return self.status is VerdictStatus.ABSTAIN

    @property
    def oldest_evidence_age(self) -> timedelta | None:
        if self.oldest_evidence_age_seconds is None:
            return None
        return timedelta(seconds=self.oldest_evidence_age_seconds)

    def failed_checks(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if not c.passed)

    def explain(self) -> str:
        """One line for logs and the provenance panel."""
        if self.abstained:
            return f"abstained: {'; '.join(self.abstain_reasons)}"
        caveats = f" ({len(self.caveats)} caveat(s))" if self.caveats else ""
        return f"{self.status}{caveats}; {len(self.failed_checks())} check(s) failed"
