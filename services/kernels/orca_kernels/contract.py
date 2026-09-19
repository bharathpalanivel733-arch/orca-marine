"""The kernel result contract (PLAN.md Phase 4.6).

Every number ORCA puts in front of a fisherman comes out of a kernel, and every kernel
returns this shape. The contract exists so provenance is structural rather than
remembered: a result physically cannot exist without recording which inputs produced it,
which formula and version computed it, and how stale the evidence was.

**No language model computes, adjusts or invents any value here.** The kernels are
ordinary arithmetic over evidence records. An LLM may later *read* a `KernelResult` and
narrate it; if it were to change a number, the formula id and inputs recorded alongside
would no longer reproduce it, and :meth:`KernelResult.verify` would fail. That is the
mechanism, not a policy statement.

Abstention is a first-class result, not an error. A kernel that cannot safely answer
returns a result with `value is None` and an `abstain_reason`. For a life-safety system,
"I will not answer, and here is why" is a correct output — the demo rehearses it
deliberately (DEMO.md §2).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Self

from orca_schemas import OrcaModel
from pydantic import Field, field_validator, model_validator


class AbstainReason(StrEnum):
    """Why a kernel declined to produce a value.

    Each is a distinct operational situation calling for a different response, so they
    are never collapsed into a generic failure.
    """

    STALE_INPUT = "stale_input"
    MISSING_REQUIRED_INPUT = "missing_required_input"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    OUT_OF_COVERAGE = "out_of_coverage"
    CONSTRAINT_VIOLATION = "constraint_violation"


class InputRole(StrEnum):
    """Whether a kernel can proceed without an input.

    ``REQUIRED`` inputs trigger hard abstention when stale or missing. ``OPTIONAL`` ones
    widen the confidence band instead — losing lightning data should make ORCA less sure,
    not silent.
    """

    REQUIRED = "required"
    OPTIONAL = "optional"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must be timezone-aware"
        raise ValueError(msg)
    return value


class KernelInput(OrcaModel):
    """One evidence item a kernel consumed, with enough detail to re-derive the result."""

    name: str = Field(description="Role in the formula, e.g. 'significant_wave_height'.")
    value: float | None
    unit: str
    source: str
    issued_time: datetime | None = Field(
        default=None, description="None for static inputs such as a vessel profile."
    )
    provenance_id: str | None = None
    role: InputRole = InputRole.REQUIRED
    stale: bool = False
    age_seconds: float | None = Field(default=None, ge=0)

    _aware = field_validator("issued_time")(lambda v: _require_aware(v) if v is not None else None)

    @property
    def missing(self) -> bool:
        return self.value is None

    @property
    def blocks_result(self) -> bool:
        """Whether this input's absence or staleness must stop the kernel."""
        return self.role is InputRole.REQUIRED and (self.missing or self.stale)


class Staleness(OrcaModel):
    """How old the evidence behind a result was.

    Reported on every result, including abstentions — the age is exactly what makes an
    abstention explainable ("nearest reliable data is 6 h old").
    """

    max_age_seconds: float = Field(ge=0, description="Age of the oldest input used.")
    evaluated_at: datetime
    stale_inputs: tuple[str, ...] = ()

    _aware = field_validator("evaluated_at")(_require_aware)

    @property
    def max_age(self) -> timedelta:
        return timedelta(seconds=self.max_age_seconds)

    @property
    def max_age_hours(self) -> float:
        return self.max_age_seconds / 3600.0

    @property
    def any_stale(self) -> bool:
        return bool(self.stale_inputs)


class ConfidenceBand(OrcaModel):
    """An interval around a value, with the reason it is that wide.

    Never symmetric by assumption: a safety score near 0 or 100 has an asymmetric band,
    and pretending otherwise would overstate certainty at exactly the extremes where a
    decision flips.
    """

    lower: float
    upper: float
    basis: str = Field(description="Why the band is this wide, in one phrase.")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.lower > self.upper:
            msg = f"lower ({self.lower}) must not exceed upper ({self.upper})"
            raise ValueError(msg)
        return self

    @property
    def width(self) -> float:
        return self.upper - self.lower


class KernelResult(OrcaModel):
    """What every deterministic kernel returns (PLAN.md Phase 4.6)."""

    kernel: str = Field(description="Which kernel produced this, e.g. 'marine_safety_score'.")
    formula_id: str = Field(description="Stable id of the formula, for replay and audit.")
    formula_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="Semantic version. A changed number with an unchanged version is a bug.",
    )
    value: float | None = Field(description="None exactly when the kernel abstained.")
    unit: str
    inputs: tuple[KernelInput, ...]
    staleness: Staleness
    confidence: ConfidenceBand | None = None
    abstain_reason: AbstainReason | None = None
    detail: str | None = Field(
        default=None, description="Human-readable explanation, required when abstaining."
    )
    extras: dict[str, Any] = Field(
        default_factory=dict,
        description="Kernel-specific structured output (bands, rankings, waypoints).",
    )

    @model_validator(mode="after")
    def _abstention_is_coherent(self) -> Self:
        if self.abstain_reason is not None:
            if self.value is not None:
                msg = "an abstaining kernel must not return a value"
                raise ValueError(msg)
            if not self.detail:
                msg = "an abstention must explain itself"
                raise ValueError(msg)
        elif self.value is None:
            msg = "a value of None requires an abstain_reason"
            raise ValueError(msg)
        return self

    @property
    def abstained(self) -> bool:
        return self.abstain_reason is not None

    def input_named(self, name: str) -> KernelInput | None:
        for item in self.inputs:
            if item.name == name:
                return item
        return None

    def fingerprint(self) -> str:
        """A hash over formula identity, inputs and value.

        Two runs of the same formula over the same inputs must produce the same
        fingerprint. Phase 6.4's replay compares these, which is what turns "the LLM did
        not touch the number" from a claim into something checkable.
        """
        payload = {
            "kernel": self.kernel,
            "formula_id": self.formula_id,
            "formula_version": self.formula_version,
            "value": self.value,
            "unit": self.unit,
            "inputs": [
                {"name": i.name, "value": i.value, "unit": i.unit, "source": i.source}
                for i in sorted(self.inputs, key=lambda i: i.name)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def verify(self, recomputed: KernelResult) -> bool:
        """Whether a recomputation reproduces this result exactly."""
        return self.fingerprint() == recomputed.fingerprint()


def evaluate_staleness(inputs: tuple[KernelInput, ...], *, evaluated_at: datetime) -> Staleness:
    """Summarise input ages for a result."""
    ages = [i.age_seconds for i in inputs if i.age_seconds is not None]
    return Staleness(
        max_age_seconds=max(ages) if ages else 0.0,
        evaluated_at=evaluated_at,
        stale_inputs=tuple(sorted(i.name for i in inputs if i.stale)),
    )


def abstain(
    *,
    kernel: str,
    formula_id: str,
    formula_version: str,
    unit: str,
    inputs: tuple[KernelInput, ...],
    evaluated_at: datetime,
    reason: AbstainReason,
    detail: str,
) -> KernelResult:
    """Build an abstention result. Used by every kernel so the shape never varies."""
    return KernelResult(
        kernel=kernel,
        formula_id=formula_id,
        formula_version=formula_version,
        value=None,
        unit=unit,
        inputs=inputs,
        staleness=evaluate_staleness(inputs, evaluated_at=evaluated_at),
        abstain_reason=reason,
        detail=detail,
    )


def abstain_if_blocked(
    *,
    kernel: str,
    formula_id: str,
    formula_version: str,
    unit: str,
    inputs: tuple[KernelInput, ...],
    evaluated_at: datetime,
) -> KernelResult | None:
    """Abstain when any required input is missing or stale, else return None.

    Centralised so no kernel can forget the check or implement it slightly differently.
    """
    blocking = [i for i in inputs if i.blocks_result]
    if not blocking:
        return None

    stale = [i for i in blocking if i.stale]
    if stale:
        oldest = max(stale, key=lambda i: i.age_seconds or 0.0)
        hours = (oldest.age_seconds or 0.0) / 3600.0
        return abstain(
            kernel=kernel,
            formula_id=formula_id,
            formula_version=formula_version,
            unit=unit,
            inputs=inputs,
            evaluated_at=evaluated_at,
            reason=AbstainReason.STALE_INPUT,
            detail=(
                f"cannot answer safely: {oldest.name} from {oldest.source} is "
                f"{hours:.1f} h old, beyond its cadence"
            ),
        )

    missing = sorted(i.name for i in blocking if i.missing)
    return abstain(
        kernel=kernel,
        formula_id=formula_id,
        formula_version=formula_version,
        unit=unit,
        inputs=inputs,
        evaluated_at=evaluated_at,
        reason=AbstainReason.MISSING_REQUIRED_INPUT,
        detail=f"cannot answer safely: required input(s) missing: {', '.join(missing)}",
    )
