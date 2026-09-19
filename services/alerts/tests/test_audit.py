"""Alert audit records (PLAN.md Phase 8.6).

The record exists to answer the questions asked after an incident, and the two hardest ones
are not about alerts that were sent:

* **Why was this boat not warned?** Only a record of the *suppressed* and *clear*
  evaluations can answer it, so those are written too.
* **Did the warning actually arrive?** Sent and delivered are different facts, and a system
  that conflates them reports a perfect record for a fisherman who received nothing.

The provenance graph reuses the Phase 6 structure rather than an alert-specific format, so
an alert is auditable by the same machinery as an answered query — and so "which decisions
read this bad payload" finds alerts too.
"""

from __future__ import annotations

from datetime import timedelta

from _alert_fixtures import NOW, result
from orca_trust import ProvenanceNodeKind

from orca_alerts import (
    ALERT_FORMULA_ID,
    ALERT_FORMULA_VERSION,
    AlertAuditRecord,
    AlertDeduplicator,
    AlertKey,
    CapSeverity,
    DecisionKind,
    DeliveryAttempt,
    DeliveryChannelKind,
    DeliveryOutcome,
    DeliveryReport,
    TickAudit,
    TriggerId,
    TriggerOutcome,
    build_alert_provenance,
)

KEY = AlertKey("u-rmd-1", TriggerId.WAVE_HEIGHT)


def decision(kind: DecisionKind = DecisionKind.SEND_NEW):
    from orca_alerts import Decision

    return Decision(kind, KEY, "because the test says so")


def record(**overrides: object) -> AlertAuditRecord:
    params: dict[str, object] = {
        "run_id": "tick-1",
        "user_id": "u-rmd-1",
        "evaluated_at": NOW,
        "trigger_result": result(),
        "decision": decision(),
    }
    params.update(overrides)
    return AlertAuditRecord(**params)  # type: ignore[arg-type]


def delivered_report() -> DeliveryReport:
    return DeliveryReport(
        user_id="u-rmd-1",
        alert_identifier="orca.a.1",
        attempts=(
            DeliveryAttempt(DeliveryChannelKind.IN_APP, DeliveryOutcome.DELIVERED, "u-rmd-1"),
        ),
    )


def failed_report() -> DeliveryReport:
    return DeliveryReport(
        user_id="u-rmd-1",
        alert_identifier="orca.a.1",
        attempts=(
            DeliveryAttempt(
                DeliveryChannelKind.FCM, DeliveryOutcome.UNCONFIGURED, "token", "no credentials"
            ),
        ),
    )


class TestProvenanceGraph:
    def test_the_chain_runs_dataset_to_output(self) -> None:
        graph = build_alert_provenance(
            run_id="tick-1:u1",
            user_id="u1",
            result=result(),
            evaluated_at=NOW,
            dataset_id="incois_osf",
        )

        kinds = [n.kind for n in graph.nodes]
        assert ProvenanceNodeKind.DATASET in kinds
        assert ProvenanceNodeKind.AGENT in kinds
        assert ProvenanceNodeKind.FORMULA in kinds
        assert ProvenanceNodeKind.OUTPUT in kinds

    def test_the_formula_node_is_versioned(self) -> None:
        """Thresholds are part of the decision; alerts from either side of a change differ."""
        graph = build_alert_provenance(
            run_id="r", user_id="u1", result=result(), evaluated_at=NOW, dataset_id="incois_osf"
        )

        formula = next(n for n in graph.nodes if n.kind is ProvenanceNodeKind.FORMULA)
        assert formula.attributes["formula_id"] == ALERT_FORMULA_ID
        assert formula.attributes["formula_version"] == ALERT_FORMULA_VERSION

    def test_the_thresholds_that_fired_are_recorded(self) -> None:
        """PLAN.md 8.6 asks for exactly this."""
        graph = build_alert_provenance(
            run_id="r", user_id="u1", result=result(), evaluated_at=NOW, dataset_id="incois_osf"
        )

        formula = next(n for n in graph.nodes if n.kind is ProvenanceNodeKind.FORMULA)
        assert formula.attributes["threshold"] == {"avoid_m": 2.0}
        assert formula.attributes["threshold_source"] == "test"

    def test_a_payload_hash_is_included_when_one_was_read(self) -> None:
        graph = build_alert_provenance(
            run_id="r",
            user_id="u1",
            result=result(),
            evaluated_at=NOW,
            dataset_id="incois_osf",
            payload_sha256="a" * 64,
        )

        payload = next(n for n in graph.nodes if n.kind is ProvenanceNodeKind.RAW_PAYLOAD)
        assert payload.attributes["sha256"] == "a" * 64

    def test_no_payload_hash_is_fabricated_when_nothing_was_fetched(self) -> None:
        """A geofence verdict reads a position and a surveyed line; nothing is downloaded."""
        graph = build_alert_provenance(
            run_id="r",
            user_id="u1",
            result=result(trigger_id=TriggerId.GEOFENCE_PROXIMITY),
            evaluated_at=NOW,
            dataset_id="imbl_1974_1976",
        )

        assert not [n for n in graph.nodes if n.kind is ProvenanceNodeKind.RAW_PAYLOAD]

    def test_the_graph_has_a_stable_fingerprint(self) -> None:
        """Same inputs, same fingerprint — the Phase 6 replay property, carried into alerts."""
        fingerprints = {
            build_alert_provenance(
                run_id="r",
                user_id="u1",
                result=result(),
                evaluated_at=NOW,
                dataset_id="incois_osf",
            ).fingerprint()
            for _ in range(10)
        }

        assert len(fingerprints) == 1


class TestAuditRecord:
    def test_a_sent_alert_is_recorded_as_sent(self) -> None:
        from test_cap import alert

        assert record(alert=alert()).was_sent

    def test_a_suppressed_evaluation_is_still_recorded(self) -> None:
        """The record that answers "why was this boat not warned"."""
        suppressed = record(decision=decision(DecisionKind.SUPPRESS_DUPLICATE))

        assert suppressed.suppressed
        assert not suppressed.was_sent

    def test_sent_is_not_delivered(self) -> None:
        """Conflating them reports a perfect record for someone who received nothing."""
        from test_cap import alert

        emitted = record(alert=alert(), delivery=failed_report())

        assert emitted.was_sent
        assert not emitted.was_delivered

    def test_a_delivered_alert_reports_both(self) -> None:
        from test_cap import alert

        arrived = record(alert=alert(), delivery=delivered_report())

        assert arrived.was_sent
        assert arrived.was_delivered

    def test_why_explains_the_arithmetic_and_the_decision_together(self) -> None:
        """Either alone misleads: a fired threshold reads as a warning that went out."""
        explanation = record(decision=decision(DecisionKind.SUPPRESS_DUPLICATE)).why()

        assert "vessel.wave_height" in explanation
        assert "warning" in explanation
        assert "suppress_duplicate" in explanation

    def test_the_flat_form_carries_the_observed_and_the_threshold(self) -> None:
        from test_cap import alert

        flat = record(alert=alert(), delivery=delivered_report()).as_dict()

        assert flat["trigger_id"] == "vessel.wave_height"
        assert flat["threshold"] == {"avoid_m": 2.0}
        assert flat["threshold_source"] == "test"
        assert flat["delivered_channels"] == ["in_app"]
        assert flat["sent"] is True
        assert flat["delivered"] is True

    def test_the_flat_form_records_every_delivery_attempt(self) -> None:
        """"Delivery failed" is useless without saying which channel and why."""
        from test_cap import alert

        flat = record(alert=alert(), delivery=failed_report()).as_dict()

        assert flat["delivery_attempts"] == [
            {"channel": "fcm", "outcome": "unconfigured", "detail": "no credentials"}
        ]

    def test_the_flat_form_carries_the_provenance_fingerprint(self) -> None:
        graph = build_alert_provenance(
            run_id="r", user_id="u1", result=result(), evaluated_at=NOW, dataset_id="incois_osf"
        )

        flat = record(provenance=graph).as_dict()

        assert flat["provenance_fingerprint"] == graph.fingerprint()


class TestTickAudit:
    def make(self, records: tuple[AlertAuditRecord, ...], skipped=()) -> TickAudit:
        return TickAudit(
            run_id="tick-1",
            started_at=NOW,
            records=records,
            skipped=skipped,
            subscribers_evaluated=len(records),
        )

    def test_it_separates_sent_suppressed_and_delivered(self) -> None:
        from test_cap import alert

        audit = self.make(
            (
                record(alert=alert(), delivery=delivered_report()),
                record(decision=decision(DecisionKind.SUPPRESS_DUPLICATE)),
                record(alert=alert(), delivery=failed_report()),
            )
        )

        assert len(audit.sent) == 2
        assert len(audit.suppressed) == 1
        assert len(audit.delivered) == 1
        assert len(audit.undelivered) == 1

    def test_undelivered_is_the_list_that_needs_acting_on(self) -> None:
        from test_cap import alert

        audit = self.make((record(alert=alert(), delivery=failed_report()),))

        assert audit.undelivered[0].user_id == "u-rmd-1"

    def test_skips_are_reported_with_their_reasons(self) -> None:
        """"No alerts sent" means two opposite things without this."""
        audit = self.make(
            (), skipped=(("u2", "ashore"), ("u3", "stale_position"), ("u4", "ashore"))
        )

        summary = audit.summary()

        assert summary["subscribers_skipped"] == 3
        assert summary["skip_reasons"] == {"ashore": 2, "stale_position": 1}

    def test_the_summary_counts_everything_an_operator_needs(self) -> None:
        from test_cap import alert

        audit = self.make(
            (
                record(alert=alert(), delivery=delivered_report()),
                record(decision=decision(DecisionKind.SUPPRESS_DUPLICATE)),
            )
        )

        summary = audit.summary()

        assert summary["rules_evaluated"] == 2
        assert summary["alerts_sent"] == 1
        assert summary["alerts_delivered"] == 1
        assert summary["alerts_suppressed"] == 1


class TestAuditAcrossTicks:
    def test_the_suppressed_record_names_the_alert_it_duplicated(self) -> None:
        """Following a suppression back to the alert that caused it is the audit's job."""
        dedup = AlertDeduplicator()
        fired = result()
        dedup.commit(KEY, identifier="orca.a.1", severity=CapSeverity.SEVERE, result=fired, now=NOW)

        second = dedup.decide(
            KEY, fired, CapSeverity.SEVERE, now=NOW + timedelta(minutes=3)
        )
        entry = record(decision=second)

        assert entry.suppressed
        assert second.previous is not None
        assert second.previous.identifier == "orca.a.1"

    def test_an_escalation_record_shows_the_severity_that_changed(self) -> None:
        dedup = AlertDeduplicator()
        watch = result(outcome=TriggerOutcome.WATCH)
        dedup.commit(
            KEY, identifier="orca.a.1", severity=CapSeverity.MODERATE, result=watch, now=NOW
        )

        escalated = dedup.decide(
            KEY,
            result(outcome=TriggerOutcome.EMERGENCY),
            CapSeverity.EXTREME,
            now=NOW + timedelta(minutes=1),
        )

        assert escalated.is_escalation
        assert escalated.previous is not None
        assert escalated.previous.severity is CapSeverity.MODERATE
