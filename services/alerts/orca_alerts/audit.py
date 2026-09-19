"""Alert audit records (PLAN.md Phase 8.6).

Every alert is stored with **the thresholds that fired it** and a provenance graph, so the
question that always follows an incident — *why did ORCA warn this boat, and on what?* — has
an answer that is a record rather than a reconstruction.

The audit record is deliberately richer than a log line, and each part earns its place by a
question it answers:

* **The trigger result** — the observed value, the threshold and where that threshold came
  from. Answers "why did it fire".
* **The dedup decision** — sent, suppressed, escalated, and the reason. Answers the harder
  question, *why did it NOT fire*, which is the one asked after someone was not warned.
* **The delivery report** — which channels were attempted and what each returned. Answers
  "did it actually reach them", which is different from "did we send it".
* **The provenance graph** — dataset → payload hash → agent → formula → output, reusing the
  Phase 6 structure so an alert is auditable by exactly the same machinery as an answered
  query, and replayable from the same archived bytes.

The distinction between *sent* and *delivered* is kept sharp throughout. A system that
records an alert as handled when it was merely queued will report a perfect record for a
fisherman who received nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from orca_trust import ProvenanceBuilder, ProvenanceGraph, ProvenanceNodeKind

from orca_alerts.cap import CapAlert, CapSeverity
from orca_alerts.dedup import Decision, DecisionKind
from orca_alerts.delivery import DeliveryReport
from orca_alerts.triggers import TriggerResult

# The formula id under which alert triggering is recorded in provenance. Versioned like a
# kernel because the thresholds are part of the decision: if they change, alerts from before
# and after are not comparable, and a replay must be able to tell which applied.
ALERT_FORMULA_ID = "orca.alerts.trigger_evaluation"
ALERT_FORMULA_VERSION = "1.0.0"


@dataclass(frozen=True)
class AlertAuditRecord:
    """One evaluation of one rule for one subscriber, whatever the outcome.

    Written for suppressed alerts too. An audit trail that records only what was sent cannot
    answer why somebody was not warned, which is the question that actually gets asked.
    """

    run_id: str
    user_id: str
    evaluated_at: datetime
    trigger_result: TriggerResult
    decision: Decision
    alert: CapAlert | None = None
    delivery: DeliveryReport | None = None
    provenance: ProvenanceGraph | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def was_sent(self) -> bool:
        """Whether an alert was emitted. Not the same as whether it arrived."""
        return self.alert is not None and self.decision.should_send

    @property
    def was_delivered(self) -> bool:
        """Whether it reached the subscriber on at least one channel."""
        return self.delivery is not None and self.delivery.delivered

    @property
    def severity(self) -> CapSeverity | None:
        return self.alert.max_severity if self.alert is not None else None

    @property
    def suppressed(self) -> bool:
        return self.decision.kind is DecisionKind.SUPPRESS_DUPLICATE

    def why(self) -> str:
        """One sentence explaining this record, for an operations view or a jury.

        The arithmetic and the decision together, because either alone is misleading: a
        fired threshold that was suppressed reads as a warning that went out, and a
        suppression reason without the threshold reads as an arbitrary silence.
        """
        return (
            f"{self.trigger_result.trigger_id.value}: {self.trigger_result.detail} "
            f"[{self.trigger_result.outcome.value}] → {self.decision.kind.value} "
            f"({self.decision.reason})"
        )

    def as_dict(self) -> dict[str, Any]:
        """Flat form for storage and for the authority-side audit view."""
        return {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "trigger_id": self.trigger_result.trigger_id.value,
            "outcome": self.trigger_result.outcome.value,
            "detail": self.trigger_result.detail,
            "observed": self.trigger_result.observed,
            "threshold": self.trigger_result.threshold,
            "threshold_source": self.trigger_result.threshold_source,
            "evidence_age_seconds": self.trigger_result.evidence_age_seconds,
            "decision": self.decision.kind.value,
            "decision_reason": self.decision.reason,
            "alert_identifier": self.alert.identifier if self.alert else None,
            "severity": self.severity.value if self.severity else None,
            "sent": self.was_sent,
            "delivered": self.was_delivered,
            "delivered_channels": [
                c.value for c in (self.delivery.delivered_channels if self.delivery else ())
            ],
            "delivery_attempts": [
                {
                    "channel": a.channel.value,
                    "outcome": a.outcome.value,
                    "detail": a.detail,
                }
                for a in (self.delivery.attempts if self.delivery else ())
            ],
            "provenance_fingerprint": (
                self.provenance.fingerprint() if self.provenance is not None else None
            ),
            "notes": list(self.notes),
        }


def build_alert_provenance(
    *,
    run_id: str,
    user_id: str,
    result: TriggerResult,
    evaluated_at: datetime,
    dataset_id: str,
    payload_sha256: str | None = None,
) -> ProvenanceGraph:
    """The provenance chain behind one alert.

    Reuses the Phase 6 graph rather than inventing an alert-specific format, so an alert is
    auditable and replayable by the same machinery as an answered question — and so the
    "which decisions used this bad payload" query finds alerts too, not only queries.

    ``payload_sha256`` is optional because not every trigger reads an archived payload: a
    geofence proximity is computed from a position and a surveyed boundary, with nothing
    fetched. Fabricating a hash to make the chain look complete would defeat the purpose.
    """
    builder = ProvenanceBuilder(run_id)

    builder.add(
        node_id=f"dataset:{dataset_id}",
        kind=ProvenanceNodeKind.DATASET,
        label=dataset_id,
        occurred_at=evaluated_at,
        attributes={"dataset_id": dataset_id, "threshold_source": result.threshold_source},
    )

    upstream = f"dataset:{dataset_id}"
    if payload_sha256 is not None:
        builder.add(
            node_id=f"payload:{payload_sha256[:16]}",
            kind=ProvenanceNodeKind.RAW_PAYLOAD,
            label="archived upstream payload",
            occurred_at=evaluated_at,
            attributes={"sha256": payload_sha256},
            derives_from=upstream,
        )
        upstream = f"payload:{payload_sha256[:16]}"

    builder.add(
        node_id="agent:alert_scheduler",
        kind=ProvenanceNodeKind.AGENT,
        label="alert scheduler",
        occurred_at=evaluated_at,
        attributes={"user_id": user_id},
        derives_from=upstream,
    )
    builder.add(
        node_id=f"formula:{result.trigger_id.value}",
        kind=ProvenanceNodeKind.FORMULA,
        label=result.trigger_id.value,
        occurred_at=evaluated_at,
        attributes={
            "formula_id": ALERT_FORMULA_ID,
            "formula_version": ALERT_FORMULA_VERSION,
            "trigger_id": result.trigger_id.value,
            "threshold": result.threshold,
            "threshold_source": result.threshold_source,
        },
        derives_from="agent:alert_scheduler",
    )
    builder.add(
        node_id=f"out:{result.trigger_id.value}",
        kind=ProvenanceNodeKind.OUTPUT,
        label=f"{result.trigger_id.value} outcome",
        occurred_at=evaluated_at,
        attributes={"outcome": result.outcome.value, "observed": result.observed},
        derives_from=f"formula:{result.trigger_id.value}",
    )

    return builder.build(recorded_at=evaluated_at)


@dataclass(frozen=True)
class TickAudit:
    """Everything one scheduled evaluation did.

    The operations view for a single tick. Its most useful property is the one that is easy
    to omit: ``skipped``, so "no alerts sent" can be read as *conditions were safe* or *we
    could not assess anyone*, which are opposite situations.
    """

    run_id: str
    started_at: datetime
    records: tuple[AlertAuditRecord, ...] = field(default_factory=tuple)
    skipped: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    subscribers_evaluated: int = 0

    @property
    def sent(self) -> tuple[AlertAuditRecord, ...]:
        return tuple(r for r in self.records if r.was_sent)

    @property
    def suppressed(self) -> tuple[AlertAuditRecord, ...]:
        return tuple(r for r in self.records if r.suppressed)

    @property
    def delivered(self) -> tuple[AlertAuditRecord, ...]:
        return tuple(r for r in self.records if r.was_delivered)

    @property
    def undelivered(self) -> tuple[AlertAuditRecord, ...]:
        """Alerts that were emitted but reached nobody — the list that needs acting on."""
        return tuple(r for r in self.records if r.was_sent and not r.was_delivered)

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "subscribers_evaluated": self.subscribers_evaluated,
            "subscribers_skipped": len(self.skipped),
            "skip_reasons": _counts(reason for _, reason in self.skipped),
            "rules_evaluated": len(self.records),
            "alerts_sent": len(self.sent),
            "alerts_delivered": len(self.delivered),
            "alerts_undelivered": len(self.undelivered),
            "alerts_suppressed": len(self.suppressed),
        }


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts
