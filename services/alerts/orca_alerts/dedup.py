"""Deduplication and escalation (PLAN.md Phase 8.3).

The scheduler runs every few minutes. Conditions change slowly. Without suppression, a
fisherman in a rough sea receives the same warning twenty times an hour, mutes ORCA, and is
then unreachable when the cyclone arrives. **Alert fatigue is the failure mode that makes a
proactive system worse than none**, so suppression is not an optimisation here — it is what
keeps the channel usable.

But suppression has an obvious way to kill someone: staying quiet while the situation gets
worse. So the policy has two halves, and the second always wins:

* **Deduplicate** on what the alert *says* — the same rule, at the same severity, about the
  same hazard, within a per-severity window. Nothing new is being communicated, so nothing
  is sent.
* **Escalate immediately**, whatever the window. A rise in severity, or a drift warning
  whose time-to-boundary has materially shortened, is new information and goes out at once
  as a CAP ``Update`` referencing what it supersedes.

There is a third case that is easy to forget and matters to a person at sea: **all-clear**.
When a fired rule returns to clear, the suppression record is retired and — for the severe
ones — a cancellation is sent, so "no more alerts" is distinguishable from "the app died".

The state here is deliberately a plain in-memory structure with an explicit clock. A
scheduler that suppressed differently depending on how long a process had been running
would be untestable, and this is exactly the logic that must behave identically on a laptop
and in production.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from orca_alerts.cap import SEVERITY_ORDER, CapSeverity
from orca_alerts.triggers import OUTCOME_RANK, TriggerId, TriggerOutcome, TriggerResult

# How long the same alert stays suppressed, by severity.
#
# Inverted against severity on purpose: a moderate warning repeated every twenty minutes is
# noise, while an Extreme alert is worth repeating every few minutes because the recipient
# may be busy saving their boat and missing notifications.
SUPPRESSION_WINDOW: dict[CapSeverity, timedelta] = {
    CapSeverity.EXTREME: timedelta(minutes=5),
    CapSeverity.SEVERE: timedelta(minutes=15),
    CapSeverity.MODERATE: timedelta(minutes=30),
    CapSeverity.MINOR: timedelta(hours=2),
    CapSeverity.UNKNOWN: timedelta(hours=1),
}

# A drift alert re-sends when the remaining time has fallen by this fraction, even inside
# the window. "Twenty minutes to the boundary" and "eight minutes to the boundary" are
# different instructions, and a suppression that treats them as one repeat is wrong.
DRIFT_RESEND_FRACTION = 0.5


class DecisionKind(StrEnum):
    """What the deduplicator decided to do with a candidate alert."""

    SEND_NEW = "send_new"
    SEND_ESCALATION = "send_escalation"
    SEND_UPDATE = "send_update"
    SUPPRESS_DUPLICATE = "suppress_duplicate"
    SEND_CANCEL = "send_cancel"
    NOTHING_TO_DO = "nothing_to_do"


@dataclass(frozen=True)
class AlertKey:
    """What makes two alerts "the same alert".

    Not the message text, and not a hash of the whole trigger result. Those would change
    whenever a wave height moved by a centimetre, defeating suppression entirely. The key is
    the *claim*: this user, this rule, this hazard.

    ``hazard_ref`` distinguishes two cyclones from one cyclone mentioned twice; it is empty
    for rules that concern only the vessel.
    """

    user_id: str
    trigger_id: TriggerId
    hazard_ref: str = ""

    def __str__(self) -> str:
        return f"{self.user_id}:{self.trigger_id.value}:{self.hazard_ref}"


@dataclass(frozen=True)
class AlertRecord:
    """What was last sent for a key."""

    key: AlertKey
    identifier: str
    severity: CapSeverity
    outcome: TriggerOutcome
    sent_at: datetime
    # Kept so a drift re-send can compare against what the recipient was last told.
    time_to_boundary_minutes: float | None = None
    send_count: int = 1

    def age(self, now: datetime) -> timedelta:
        return now - self.sent_at


@dataclass(frozen=True)
class Decision:
    """The deduplicator's verdict on one candidate."""

    kind: DecisionKind
    key: AlertKey
    reason: str
    previous: AlertRecord | None = None

    @property
    def should_send(self) -> bool:
        return self.kind in {
            DecisionKind.SEND_NEW,
            DecisionKind.SEND_ESCALATION,
            DecisionKind.SEND_UPDATE,
            DecisionKind.SEND_CANCEL,
        }

    @property
    def is_escalation(self) -> bool:
        return self.kind is DecisionKind.SEND_ESCALATION

    @property
    def references_previous(self) -> bool:
        """Whether the CAP message must reference what it supersedes."""
        return (
            self.kind
            in {
                DecisionKind.SEND_ESCALATION,
                DecisionKind.SEND_UPDATE,
                DecisionKind.SEND_CANCEL,
            }
            and self.previous is not None
        )


def hazard_ref_for(result: TriggerResult) -> str:
    """The hazard a result is about, for keying.

    Two active cyclones must not deduplicate against each other: suppressing the second
    because the first was just announced would hide a storm.
    """
    system = result.observed.get("system")
    return str(system) if system else ""


class AlertDeduplicator:
    """Decides whether a candidate alert is worth sending.

    Holds the last-sent record per key. In production this belongs in Redis so it survives a
    restart and is shared across workers; the interface is the same, and the in-memory
    implementation is what the tests drive so the *policy* is verified independently of the
    store.
    """

    def __init__(self, records: dict[str, AlertRecord] | None = None) -> None:
        self._records: dict[str, AlertRecord] = dict(records or {})

    def record_for(self, key: AlertKey) -> AlertRecord | None:
        return self._records.get(str(key))

    def decide(
        self, key: AlertKey, result: TriggerResult, severity: CapSeverity, *, now: datetime
    ) -> Decision:
        """Whether to send, and as what kind of CAP message."""
        previous = self._records.get(str(key))

        if not result.fired:
            if previous is None:
                return Decision(DecisionKind.NOTHING_TO_DO, key, "clear, and nothing outstanding")
            # A rule that has returned to clear. Cancel the severe ones explicitly so
            # silence is not mistaken for a dead app; let the minor ones simply lapse.
            if SEVERITY_ORDER[previous.severity] <= SEVERITY_ORDER[CapSeverity.SEVERE]:
                return Decision(
                    DecisionKind.SEND_CANCEL,
                    key,
                    f"{result.trigger_id.value} has returned to clear",
                    previous,
                )
            return Decision(
                DecisionKind.NOTHING_TO_DO, key, "returned to clear below the cancel threshold",
                previous,
            )

        if previous is None:
            return Decision(DecisionKind.SEND_NEW, key, "first alert for this hazard")

        # Escalation beats every window. This branch is the one that must never be
        # short-circuited by a suppression optimisation.
        if OUTCOME_RANK[result.outcome] > OUTCOME_RANK[previous.outcome]:
            return Decision(
                DecisionKind.SEND_ESCALATION,
                key,
                f"escalated from {previous.outcome.value} to {result.outcome.value}",
                previous,
            )

        if _drift_has_materially_shortened(result, previous):
            return Decision(
                DecisionKind.SEND_UPDATE,
                key,
                "time to boundary has roughly halved since the last alert",
                previous,
            )

        window = SUPPRESSION_WINDOW[severity]
        if previous.age(now) < window:
            minutes = previous.age(now).total_seconds() / 60
            return Decision(
                DecisionKind.SUPPRESS_DUPLICATE,
                key,
                (
                    f"identical {result.outcome.value} sent {minutes:.0f} min ago, inside the "
                    f"{window.total_seconds() / 60:.0f} min window for {severity.value}"
                ),
                previous,
            )

        return Decision(
            DecisionKind.SEND_UPDATE, key, "suppression window has elapsed and it is still firing",
            previous,
        )

    def commit(
        self,
        key: AlertKey,
        *,
        identifier: str,
        severity: CapSeverity,
        result: TriggerResult,
        now: datetime,
    ) -> AlertRecord:
        """Record that an alert went out. Called only after a successful send."""
        previous = self._records.get(str(key))
        record = AlertRecord(
            key=key,
            identifier=identifier,
            severity=severity,
            outcome=result.outcome,
            sent_at=now,
            time_to_boundary_minutes=_drift_minutes(result),
            send_count=(previous.send_count + 1) if previous else 1,
        )
        self._records[str(key)] = record
        return record

    def retire(self, key: AlertKey) -> AlertRecord | None:
        """Forget a key after a cancellation, so the next occurrence is a fresh alert."""
        return self._records.pop(str(key), None)

    def prune(self, now: datetime, *, older_than: timedelta = timedelta(hours=12)) -> int:
        """Drop records too old to affect any decision. Returns how many were removed."""
        stale = [k for k, r in self._records.items() if r.age(now) > older_than]
        for key in stale:
            del self._records[key]
        return len(stale)

    def __len__(self) -> int:
        return len(self._records)


def _drift_minutes(result: TriggerResult) -> float | None:
    value = result.observed.get("time_to_boundary_minutes")
    return float(value) if isinstance(value, int | float) else None


def _drift_has_materially_shortened(result: TriggerResult, previous: AlertRecord) -> bool:
    """Whether a drift alert now says something meaningfully different.

    Only applies to the drift rule: for a wave height, a changed number at the same severity
    is the same instruction, but for drift the number *is* the instruction.
    """
    if result.trigger_id is not TriggerId.GEOFENCE_DRIFT:
        return False
    current = _drift_minutes(result)
    if current is None or previous.time_to_boundary_minutes is None:
        return False
    return current <= previous.time_to_boundary_minutes * DRIFT_RESEND_FRACTION


@dataclass(frozen=True)
class SuppressionSummary:
    """What a tick suppressed, for the operations view.

    Reported because a quiet system has two explanations — nothing is wrong, or everything
    is being suppressed — and an operator who cannot tell them apart will not trust either.
    """

    evaluated: int = 0
    sent: int = 0
    suppressed: int = 0
    escalated: int = 0
    cancelled: int = 0
    by_reason: dict[str, int] = field(default_factory=dict)

    def with_decision(self, decision: Decision) -> SuppressionSummary:
        counts = dict(self.by_reason)
        counts[decision.kind.value] = counts.get(decision.kind.value, 0) + 1
        return replace(
            self,
            evaluated=self.evaluated + 1,
            sent=self.sent + (1 if decision.should_send else 0),
            suppressed=self.suppressed
            + (1 if decision.kind is DecisionKind.SUPPRESS_DUPLICATE else 0),
            escalated=self.escalated + (1 if decision.is_escalation else 0),
            cancelled=self.cancelled + (1 if decision.kind is DecisionKind.SEND_CANCEL else 0),
            by_reason=counts,
        )
