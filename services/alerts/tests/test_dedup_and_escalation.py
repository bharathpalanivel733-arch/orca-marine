"""Deduplication and escalation (PLAN.md Phase 8.3).

Two failure modes, pulling in opposite directions, and the tests exist to pin the balance
between them:

* **Too loud.** The same warning twenty times an hour gets ORCA muted, and a muted safety
  system warns nobody about anything. Suppression is what keeps the channel usable.
* **Too quiet.** Suppressing while conditions worsen is the way this component could get
  someone killed. Escalation must beat every window, unconditionally.

The second is the one worth being paranoid about, so it is tested from several directions:
a severity rise inside the window, a drift time that has halved, and the cancellation that
distinguishes "it's over" from "the app died".
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from _alert_fixtures import NOW, result

from orca_alerts import (
    SUPPRESSION_WINDOW,
    AlertDeduplicator,
    AlertKey,
    CapSeverity,
    DecisionKind,
    SuppressionSummary,
    TriggerId,
    TriggerOutcome,
    hazard_ref_for,
    severity_for,
)

KEY = AlertKey("u-rmd-1", TriggerId.WAVE_HEIGHT)


def fired(outcome: TriggerOutcome = TriggerOutcome.WARNING, **observed: object):
    return result(trigger_id=TriggerId.WAVE_HEIGHT, outcome=outcome, **observed)


def send(dedup: AlertDeduplicator, outcome: TriggerOutcome, *, at, key: AlertKey = KEY):
    """Decide and, if it would send, commit — the scheduler's own sequence."""
    trigger = fired(outcome)
    severity = severity_for(trigger)
    decision = dedup.decide(key, trigger, severity, now=at)
    if decision.should_send:
        dedup.commit(
            key, identifier=f"id-{at.isoformat()}", severity=severity, result=trigger, now=at
        )
    return decision


class TestFirstAlert:
    def test_the_first_alert_for_a_hazard_is_sent(self) -> None:
        dedup = AlertDeduplicator()

        decision = dedup.decide(KEY, fired(), CapSeverity.SEVERE, now=NOW)

        assert decision.kind is DecisionKind.SEND_NEW
        assert decision.should_send

    def test_a_clear_rule_with_nothing_outstanding_sends_nothing(self) -> None:
        dedup = AlertDeduplicator()

        decision = dedup.decide(KEY, fired(TriggerOutcome.CLEAR), CapSeverity.MINOR, now=NOW)

        assert decision.kind is DecisionKind.NOTHING_TO_DO
        assert not decision.should_send

    def test_an_unevaluated_rule_never_sends(self) -> None:
        """Not being able to look is not a reason to warn, or to reassure."""
        dedup = AlertDeduplicator()

        decision = dedup.decide(
            KEY, fired(TriggerOutcome.UNEVALUATED), CapSeverity.UNKNOWN, now=NOW
        )

        assert not decision.should_send


class TestSuppression:
    def test_an_identical_repeat_inside_the_window_is_suppressed(self) -> None:
        """The whole point: twenty identical warnings an hour is how ORCA gets muted."""
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW)

        decision = dedup.decide(
            KEY, fired(), CapSeverity.SEVERE, now=NOW + timedelta(minutes=5)
        )

        assert decision.kind is DecisionKind.SUPPRESS_DUPLICATE
        assert not decision.should_send

    def test_the_suppression_reason_names_the_window(self) -> None:
        """An operator asking why a fisherman was not warned needs the number."""
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW)

        decision = dedup.decide(
            KEY, fired(), CapSeverity.SEVERE, now=NOW + timedelta(minutes=5)
        )

        assert "15 min window" in decision.reason
        assert "Severe" in decision.reason

    def test_it_re_sends_once_the_window_has_elapsed(self) -> None:
        """Still firing after the window is worth repeating; the hazard has not gone away."""
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW)

        decision = dedup.decide(
            KEY, fired(), CapSeverity.SEVERE, now=NOW + timedelta(minutes=20)
        )

        assert decision.kind is DecisionKind.SEND_UPDATE

    @pytest.mark.parametrize(
        ("severity", "expected_minutes"),
        [
            (CapSeverity.EXTREME, 5),
            (CapSeverity.SEVERE, 15),
            (CapSeverity.MODERATE, 30),
            (CapSeverity.MINOR, 120),
        ],
    )
    def test_the_window_shortens_as_severity_rises(
        self, severity: CapSeverity, expected_minutes: int
    ) -> None:
        """An Extreme alert repeats often: the recipient may be busy saving their boat."""
        assert SUPPRESSION_WINDOW[severity] == timedelta(minutes=expected_minutes)

    def test_different_hazards_do_not_suppress_each_other(self) -> None:
        """Suppressing a second cyclone because the first was announced would hide a storm."""
        dedup = AlertDeduplicator()
        fengal = AlertKey("u1", TriggerId.CYCLONE_WIND, "Fengal")
        montha = AlertKey("u1", TriggerId.CYCLONE_WIND, "Montha")

        send(dedup, TriggerOutcome.WARNING, at=NOW, key=fengal)
        decision = dedup.decide(montha, fired(), CapSeverity.SEVERE, now=NOW)

        assert decision.kind is DecisionKind.SEND_NEW

    def test_different_users_do_not_suppress_each_other(self) -> None:
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW, key=AlertKey("u1", TriggerId.WAVE_HEIGHT))

        decision = dedup.decide(
            AlertKey("u2", TriggerId.WAVE_HEIGHT), fired(), CapSeverity.SEVERE, now=NOW
        )

        assert decision.kind is DecisionKind.SEND_NEW

    def test_different_rules_do_not_suppress_each_other(self) -> None:
        """A wind warning must not be silenced by a wave warning sent a minute earlier."""
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW, key=AlertKey("u1", TriggerId.WAVE_HEIGHT))

        decision = dedup.decide(
            AlertKey("u1", TriggerId.WIND_SPEED), fired(), CapSeverity.SEVERE, now=NOW
        )

        assert decision.kind is DecisionKind.SEND_NEW

    def test_a_changed_number_at_the_same_severity_is_still_a_duplicate(self) -> None:
        """2.4 m and 2.5 m carry the same instruction; re-sending is noise."""
        dedup = AlertDeduplicator()
        first = result(
            trigger_id=TriggerId.WAVE_HEIGHT,
            outcome=TriggerOutcome.WARNING,
            significant_wave_height_m=2.4,
        )
        dedup.commit(KEY, identifier="id-1", severity=CapSeverity.SEVERE, result=first, now=NOW)

        second = result(
            trigger_id=TriggerId.WAVE_HEIGHT,
            outcome=TriggerOutcome.WARNING,
            significant_wave_height_m=2.5,
        )
        decision = dedup.decide(
            KEY, second, CapSeverity.SEVERE, now=NOW + timedelta(minutes=3)
        )

        assert decision.kind is DecisionKind.SUPPRESS_DUPLICATE


class TestEscalation:
    def test_a_severity_rise_overrides_the_suppression_window(self) -> None:
        """The branch that must never be optimised away: conditions got worse."""
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WATCH, at=NOW)

        decision = dedup.decide(
            KEY,
            fired(TriggerOutcome.WARNING),
            CapSeverity.SEVERE,
            now=NOW + timedelta(seconds=30),
        )

        assert decision.kind is DecisionKind.SEND_ESCALATION
        assert decision.should_send
        assert decision.is_escalation

    def test_escalation_reports_the_transition(self) -> None:
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WATCH, at=NOW)

        decision = dedup.decide(
            KEY,
            fired(TriggerOutcome.EMERGENCY),
            CapSeverity.EXTREME,
            now=NOW + timedelta(minutes=1),
        )

        assert "watch to emergency" in decision.reason

    def test_escalation_references_the_alert_it_supersedes(self) -> None:
        """CAP requires it, and without it an update reads as a second warning."""
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WATCH, at=NOW)

        decision = dedup.decide(
            KEY, fired(TriggerOutcome.WARNING), CapSeverity.SEVERE, now=NOW + timedelta(minutes=1)
        )

        assert decision.references_previous
        assert decision.previous is not None

    def test_a_de_escalation_is_not_an_escalation(self) -> None:
        """Conditions improving is not news worth interrupting somebody for."""
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.EMERGENCY, at=NOW)

        decision = dedup.decide(
            KEY, fired(TriggerOutcome.WARNING), CapSeverity.SEVERE, now=NOW + timedelta(minutes=1)
        )

        assert decision.kind is DecisionKind.SUPPRESS_DUPLICATE

    def test_escalation_works_through_every_step(self) -> None:
        dedup = AlertDeduplicator()
        steps = [TriggerOutcome.WATCH, TriggerOutcome.WARNING, TriggerOutcome.EMERGENCY]
        kinds = []

        for index, outcome in enumerate(steps):
            decision = send(dedup, outcome, at=NOW + timedelta(seconds=30 * index))
            kinds.append(decision.kind)

        assert kinds == [
            DecisionKind.SEND_NEW,
            DecisionKind.SEND_ESCALATION,
            DecisionKind.SEND_ESCALATION,
        ]


class TestDriftResend:
    def drift(self, minutes: float, outcome: TriggerOutcome = TriggerOutcome.WARNING):
        return result(
            trigger_id=TriggerId.GEOFENCE_DRIFT,
            outcome=outcome,
            time_to_boundary_minutes=minutes,
        )

    def test_a_halved_time_to_boundary_re_sends_inside_the_window(self) -> None:
        """"Twenty minutes" and "eight minutes" are different instructions."""
        dedup = AlertDeduplicator()
        key = AlertKey("u1", TriggerId.GEOFENCE_DRIFT)
        dedup.commit(
            key, identifier="id-1", severity=CapSeverity.SEVERE, result=self.drift(20.0), now=NOW
        )

        decision = dedup.decide(
            key, self.drift(8.0), CapSeverity.SEVERE, now=NOW + timedelta(minutes=2)
        )

        assert decision.kind is DecisionKind.SEND_UPDATE
        assert "halved" in decision.reason

    def test_a_slightly_shorter_time_does_not_re_send(self) -> None:
        """Every tick shaving a minute off would defeat suppression entirely."""
        dedup = AlertDeduplicator()
        key = AlertKey("u1", TriggerId.GEOFENCE_DRIFT)
        dedup.commit(
            key, identifier="id-1", severity=CapSeverity.SEVERE, result=self.drift(20.0), now=NOW
        )

        decision = dedup.decide(
            key, self.drift(17.0), CapSeverity.SEVERE, now=NOW + timedelta(minutes=2)
        )

        assert decision.kind is DecisionKind.SUPPRESS_DUPLICATE

    def test_the_rule_only_applies_to_drift(self) -> None:
        """A wave height that halved at the same severity is still the same instruction."""
        dedup = AlertDeduplicator()
        first = result(
            trigger_id=TriggerId.WAVE_HEIGHT,
            outcome=TriggerOutcome.WARNING,
            time_to_boundary_minutes=20.0,
        )
        dedup.commit(KEY, identifier="id-1", severity=CapSeverity.SEVERE, result=first, now=NOW)

        second = result(
            trigger_id=TriggerId.WAVE_HEIGHT,
            outcome=TriggerOutcome.WARNING,
            time_to_boundary_minutes=5.0,
        )
        decision = dedup.decide(
            KEY, second, CapSeverity.SEVERE, now=NOW + timedelta(minutes=2)
        )

        assert decision.kind is DecisionKind.SUPPRESS_DUPLICATE


class TestAllClear:
    def test_a_severe_alert_returning_to_clear_is_cancelled(self) -> None:
        """Silence and "it's over" must be distinguishable to someone at sea."""
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW)

        decision = dedup.decide(
            KEY, fired(TriggerOutcome.CLEAR), CapSeverity.MINOR, now=NOW + timedelta(hours=1)
        )

        assert decision.kind is DecisionKind.SEND_CANCEL
        assert decision.should_send
        assert decision.references_previous

    def test_a_moderate_alert_returning_to_clear_simply_lapses(self) -> None:
        """A cancellation for every watch would itself become the noise."""
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WATCH, at=NOW)

        decision = dedup.decide(
            KEY, fired(TriggerOutcome.CLEAR), CapSeverity.MINOR, now=NOW + timedelta(hours=1)
        )

        assert decision.kind is DecisionKind.NOTHING_TO_DO

    def test_retiring_a_key_makes_the_next_occurrence_a_fresh_alert(self) -> None:
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW)
        dedup.retire(KEY)

        decision = dedup.decide(
            KEY, fired(), CapSeverity.SEVERE, now=NOW + timedelta(minutes=1)
        )

        assert decision.kind is DecisionKind.SEND_NEW


class TestStateManagement:
    def test_committing_counts_repeats(self) -> None:
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW)
        send(dedup, TriggerOutcome.WARNING, at=NOW + timedelta(minutes=20))

        record = dedup.record_for(KEY)

        assert record is not None
        assert record.send_count == 2

    def test_pruning_drops_records_too_old_to_matter(self) -> None:
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW)

        removed = dedup.prune(NOW + timedelta(days=1))

        assert removed == 1
        assert len(dedup) == 0

    def test_pruning_keeps_records_that_still_affect_decisions(self) -> None:
        dedup = AlertDeduplicator()
        send(dedup, TriggerOutcome.WARNING, at=NOW)

        assert dedup.prune(NOW + timedelta(minutes=30)) == 0

    def test_the_hazard_ref_separates_named_systems(self) -> None:
        assert hazard_ref_for(result(system="Fengal")) == "Fengal"
        assert hazard_ref_for(result()) == ""

    def test_decisions_are_deterministic(self) -> None:
        """Two identical runs of a tick must suppress identically, or the audit is noise."""
        outcomes = set()
        for _ in range(10):
            dedup = AlertDeduplicator()
            send(dedup, TriggerOutcome.WARNING, at=NOW)
            decision = dedup.decide(
                KEY, fired(), CapSeverity.SEVERE, now=NOW + timedelta(minutes=5)
            )
            outcomes.add(decision.kind)

        assert outcomes == {DecisionKind.SUPPRESS_DUPLICATE}


class TestSuppressionSummary:
    def test_it_counts_what_a_tick_did(self) -> None:
        """A quiet system has two explanations; an operator must be able to tell them apart."""
        dedup = AlertDeduplicator()
        summary = SuppressionSummary()

        first = dedup.decide(KEY, fired(), CapSeverity.SEVERE, now=NOW)
        dedup.commit(KEY, identifier="id-1", severity=CapSeverity.SEVERE, result=fired(), now=NOW)
        summary = summary.with_decision(first)

        second = dedup.decide(
            KEY, fired(), CapSeverity.SEVERE, now=NOW + timedelta(minutes=2)
        )
        summary = summary.with_decision(second)

        assert summary.evaluated == 2
        assert summary.sent == 1
        assert summary.suppressed == 1
        assert summary.by_reason["send_new"] == 1
        assert summary.by_reason["suppress_duplicate"] == 1
