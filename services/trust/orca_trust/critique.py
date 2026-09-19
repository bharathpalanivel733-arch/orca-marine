"""Independent critique — explanatory support, never the decision authority (PLAN.md 6.1).

The critique runs **after** the deterministic rules, in a **fresh context** that does not
see the reasoning that produced the answer. An adversarial critic that inherits the
generator's assumptions inherits its blind spots too.

Its authority is deliberately asymmetric, and this is enforced in code rather than by
convention:

* it **may add** a caveat, or escalate an answer to an abstention;
* it **may not** clear a blocking check, downgrade a severity, or turn an abstention into
  an answer.

The reason is simple. A critique that can argue the system out of a refusal is a
liability: it will eventually do so on the one query where the refusal was right. A
critique that can only raise doubt is useful in every direction that matters, and its
worst case is an unnecessary abstention — which is survivable.

The default implementation is :class:`NullCritic`, which returns nothing. ORCA is fully
functional with no model configured; the critique adds explanation, not capability.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from orca_trust.verdict import Caveat, TrustVerdict, VerdictStatus


@dataclass(frozen=True)
class CritiqueRequest:
    """What the critic is shown.

    Only the evidence summary and the rule outcomes — deliberately not the narrative or
    the chain of reasoning that produced the answer, so the critic forms an independent
    view rather than agreeing with one already taken.
    """

    evidence_summary: tuple[str, ...]
    check_summary: tuple[str, ...]
    proposed_status: VerdictStatus
    evaluated_at: datetime


@dataclass(frozen=True)
class CritiqueResponse:
    """What the critic returned.

    ``escalate_to_abstain`` is the strongest thing it can ask for, and even that is a
    request the composer honours rather than a state the critic sets.
    """

    commentary: str | None = None
    added_caveats: tuple[Caveat, ...] = ()
    escalate_to_abstain: bool = False
    escalation_reason: str | None = None

    def __post_init__(self) -> None:
        if self.escalate_to_abstain and not self.escalation_reason:
            msg = "an escalation must carry a reason"
            raise ValueError(msg)


class Critic(ABC):
    """The critique step behind the verifier."""

    @abstractmethod
    def review(self, request: CritiqueRequest) -> CritiqueResponse: ...


class NullCritic(Critic):
    """No critique. The default, so the trust layer needs no model to function."""

    def review(self, request: CritiqueRequest) -> CritiqueResponse:
        return CritiqueResponse()


class LlmCritic(Critic):
    """Critique backed by a language model in a fresh context.

    The model is asked for commentary and, at most, an escalation. Whatever it returns,
    :func:`apply_critique` constrains the effect — so a model that tries to clear a
    blocking check simply has no mechanism to do so.
    """

    def __init__(self, model: object, *, system_prompt: str | None = None) -> None:
        self._model = model
        self._system_prompt = system_prompt or (
            "You are an independent reviewer of marine safety evidence. You are shown the "
            "evidence and the automated check results, not the reasoning behind the answer. "
            "Point out anything the checks may have missed. You cannot approve or overturn "
            "a check; you may only raise concerns."
        )

    def review(self, request: CritiqueRequest) -> CritiqueResponse:
        # The transport is injected; a model that errors or returns nothing usable must
        # never block a verdict, because the critique is advisory by construction.
        try:
            raw = self._model.review(  # type: ignore[attr-defined]
                system=self._system_prompt,
                evidence=request.evidence_summary,
                checks=request.check_summary,
                proposed_status=request.proposed_status.value,
            )
        except Exception:  # noqa: BLE001 - advisory step must not break the decision
            return CritiqueResponse(commentary=None)

        if not isinstance(raw, dict):
            return CritiqueResponse(commentary=str(raw) if raw else None)

        escalate = bool(raw.get("escalate_to_abstain"))
        reason = raw.get("escalation_reason")
        if escalate and not reason:
            # An escalation with no reason is unusable: drop the escalation, keep the
            # commentary. Abstaining "because the model said so" is not explainable.
            escalate = False
        return CritiqueResponse(
            commentary=raw.get("commentary"),
            added_caveats=tuple(
                Caveat(kind="critique", detail=str(c)) for c in raw.get("caveats", ())
            ),
            escalate_to_abstain=escalate,
            escalation_reason=reason if escalate else None,
        )


def apply_critique(verdict: TrustVerdict, response: CritiqueResponse) -> TrustVerdict:
    """Fold a critique into a verdict, one direction only.

    Doubt is additive; confidence is not. An already-abstaining verdict cannot be talked
    into answering, and a blocking check cannot be cleared, because neither path exists
    in this function.
    """
    caveats = (*verdict.caveats, *response.added_caveats)
    status = verdict.status
    reasons = verdict.abstain_reasons

    if response.escalate_to_abstain and status is not VerdictStatus.ABSTAIN:
        status = VerdictStatus.ABSTAIN
        reasons = (*reasons, f"critique escalation: {response.escalation_reason}")
    elif caveats and status is VerdictStatus.ANSWER:
        status = VerdictStatus.ANSWER_WITH_CAVEATS

    return verdict.model_copy(
        update={
            "status": status,
            "caveats": caveats,
            "abstain_reasons": reasons,
            "critique": response.commentary,
        }
    )
