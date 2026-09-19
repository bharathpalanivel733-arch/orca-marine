"""The scheduled evaluation tick, end to end (PLAN.md Phase 8.1–8.6).

Everything wired together: rules fire, CAP is built, delivery is attempted, audit records
are written — including for the evaluations that produced nothing.

Two properties are asserted repeatedly because they are what make the subsystem trustworthy
rather than merely functional:

* **A tick is a pure function of its inputs and an explicit clock.** The same inputs produce
  the same alerts in the same order, which is what makes the audit diffable and the
  suppression testable at rest rather than only against a running scheduler.
* **Silence is always explained.** Every subscriber that could not be assessed is recorded
  with a reason, so "no alerts sent" is never ambiguous between *conditions were safe* and
  *we could not look*.
"""

from __future__ import annotations

from datetime import timedelta

from _alert_fixtures import NOW, PALK_BAY_LAT, PALK_BAY_LON, conditions, cyclone, picture
from orca_geo import ImblGeofence
from orca_kernels import VesselProfile
from orca_speech import InMemoryAudioCache, Language, build_default_chain

from orca_alerts import (
    AlertContext,
    AlertScheduler,
    CapMsgType,
    CapScope,
    CapSeverity,
    ChannelEndpoint,
    DecisionKind,
    DeliveryChannelKind,
    DeliveryRouter,
    InAppChannel,
    Subscriber,
    TickConfig,
    TriggerId,
    TriggerOutcome,
    VesselState,
    validate,
)

CONTEXT = AlertContext(
    location_name="Palk Bay",
    harbour_name="Rameswaram",
    distance_text="11.0 km",
    valid_until="18 00",
    issued_time="06 00",
    authority="INCOIS",
)


def scheduler(**kwargs: object) -> AlertScheduler:
    params: dict[str, object] = {
        "geofence": ImblGeofence(),
        "config": TickConfig(languages=(Language.TAMIL, Language.ENGLISH)),
    }
    params.update(kwargs)
    return AlertScheduler(**params)  # type: ignore[arg-type]


def rough_picture(user_id: str = "u-rmd-1"):
    return picture(conditions={user_id: conditions(wave_m=2.8, wind_ms=14.0)})


class TestTickMechanics:
    def test_a_calm_tick_sends_nothing(self, subscriber: Subscriber) -> None:
        audit = scheduler().tick(
            [subscriber],
            picture(conditions={subscriber.user_id: conditions()}),
            now=NOW,
            run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )

        assert audit.sent == ()
        assert audit.subscribers_evaluated == 1

    def test_a_rough_sea_alerts_the_vallam(self, subscriber: Subscriber) -> None:
        audit = scheduler().tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )

        fired = {r.trigger_result.trigger_id for r in audit.sent}
        assert TriggerId.WAVE_HEIGHT in fired
        assert TriggerId.WIND_SPEED in fired

    def test_every_evaluation_is_recorded_not_only_the_alerts(
        self, subscriber: Subscriber
    ) -> None:
        """The clear records are what answer "why was this boat not warned"."""
        audit = scheduler().tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )

        outcomes = {r.trigger_result.outcome for r in audit.records}
        assert TriggerOutcome.CLEAR in outcomes
        assert len(audit.records) > len(audit.sent)

    def test_the_tick_is_deterministic(self, subscriber: Subscriber) -> None:
        signatures = set()
        for _ in range(5):
            audit = scheduler().tick(
                [subscriber], rough_picture(), now=NOW, run_id="tick-1",
                contexts={subscriber.user_id: CONTEXT},
            )
            signatures.add(
                tuple((r.trigger_result.trigger_id, r.decision.kind) for r in audit.records)
            )

        assert len(signatures) == 1

    def test_rule_order_is_fixed_across_families(self, subscriber: Subscriber) -> None:
        audit = scheduler().tick(
            [subscriber],
            picture(
                cyclones=(cyclone(),),
                conditions={subscriber.user_id: conditions()},
            ),
            now=NOW,
            run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )

        ids = [r.trigger_result.trigger_id for r in audit.records]
        assert ids.index(TriggerId.CYCLONE_WIND) < ids.index(TriggerId.WAVE_HEIGHT)
        assert ids.index(TriggerId.WAVE_HEIGHT) < ids.index(TriggerId.GEOFENCE_PROXIMITY)


class TestSkipping:
    def test_a_boat_in_harbour_is_not_evaluated(self, subscriber: Subscriber) -> None:
        """A wave warning for someone tied up is how a safety channel gets muted."""
        ashore = Subscriber(
            **{**subscriber.__dict__, "state": VesselState(
                lat=PALK_BAY_LAT, lon=PALK_BAY_LON, reported_at=NOW, at_sea=False
            )}
        )

        audit = scheduler().tick([ashore], rough_picture(), now=NOW, run_id="tick-1")

        assert audit.records == ()
        assert audit.skipped == (("u-rmd-1", "ashore"),)

    def test_a_stale_position_is_skipped_with_its_reason(self, subscriber: Subscriber) -> None:
        """A 40-minute-old fix could put a drifting boat over the boundary unnoticed."""
        stale = Subscriber(
            **{**subscriber.__dict__, "state": VesselState(
                lat=PALK_BAY_LAT,
                lon=PALK_BAY_LON,
                reported_at=NOW - timedelta(minutes=40),
            )}
        )

        audit = scheduler().tick([stale], rough_picture(), now=NOW, run_id="tick-1")

        assert audit.skipped == (("u-rmd-1", "stale_position"),)

    def test_a_subscriber_with_no_position_is_skipped(self, vallam: VesselProfile) -> None:
        unknown = Subscriber(user_id="u9", vessel=vallam, language=Language.TAMIL)

        audit = scheduler().tick([unknown], rough_picture(), now=NOW, run_id="tick-1")

        assert audit.skipped == (("u9", "no_position"),)

    def test_an_unreachable_subscriber_is_skipped(self, subscriber: Subscriber) -> None:
        """Evaluating someone with no live endpoint burns work and delivers nothing."""
        unreachable = Subscriber(**{**subscriber.__dict__, "endpoints": ()})

        audit = scheduler().tick([unreachable], rough_picture(), now=NOW, run_id="tick-1")

        assert audit.skipped == (("u-rmd-1", "unreachable"),)

    def test_a_quiet_tick_is_distinguishable_from_a_blind_one(
        self, subscriber: Subscriber
    ) -> None:
        """The property an operations view lives or dies by."""
        ashore = Subscriber(
            **{**subscriber.__dict__, "state": VesselState(
                lat=PALK_BAY_LAT, lon=PALK_BAY_LON, reported_at=NOW, at_sea=False
            )}
        )

        blind = scheduler().tick([ashore], rough_picture(), now=NOW, run_id="t1").summary()
        calm = scheduler().tick(
            [subscriber],
            picture(conditions={subscriber.user_id: conditions()}),
            now=NOW,
            run_id="t2",
            contexts={subscriber.user_id: CONTEXT},
        ).summary()

        assert blind["alerts_sent"] == calm["alerts_sent"] == 0
        assert blind["subscribers_skipped"] == 1
        assert calm["subscribers_skipped"] == 0


class TestCapOutput:
    def alert_for(self, subscriber: Subscriber):
        audit = scheduler().tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )
        record = next(r for r in audit.sent if r.trigger_result.trigger_id is TriggerId.WAVE_HEIGHT)
        assert record.alert is not None
        return record.alert

    def test_the_alert_validates_structurally(self, subscriber: Subscriber) -> None:
        assert validate(self.alert_for(subscriber)) == ()

    def test_a_vessel_alert_is_private_scope(self, subscriber: Subscriber) -> None:
        """A vessel's position is not public information."""
        alert = self.alert_for(subscriber)

        assert alert.scope is CapScope.PRIVATE
        assert alert.addresses == ("u-rmd-1",)

    def test_the_subscribers_language_comes_first(self, subscriber: Subscriber) -> None:
        """Consumers that read only the first info block must get the readable one."""
        alert = self.alert_for(subscriber)

        assert alert.info[0].language is Language.TAMIL
        assert alert.info[1].language is Language.ENGLISH

    def test_the_severity_follows_the_rule_outcome(self, subscriber: Subscriber) -> None:
        alert = self.alert_for(subscriber)

        assert alert.max_severity is CapSeverity.SEVERE

    def test_the_arithmetic_that_fired_travels_in_the_cap_parameters(
        self, subscriber: Subscriber
    ) -> None:
        """An alert must be explainable from the message itself, not only from a log."""
        alert = self.alert_for(subscriber)
        names = {name for name, _ in alert.info[0].parameters}

        assert {"trigger_id", "observed", "threshold", "threshold_source"} <= names

    def test_the_numbers_survive_into_the_spoken_text(self, subscriber: Subscriber) -> None:
        """Phase 7's guarantee, carried into the alert path."""
        audit = scheduler(router=DeliveryRouter([InAppChannel()])).tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )
        record = next(r for r in audit.sent if r.trigger_result.trigger_id is TriggerId.WAVE_HEIGHT)

        assert record.alert is not None
        assert "2.8" in record.alert.info[0].description


class TestEscalationAcrossTicks:
    def run_tick(self, sched: AlertScheduler, subscriber: Subscriber, wave_m: float, at):
        # The position fix moves with the clock. A real handset reports every few minutes,
        # and a fixture whose fix stayed at the first tick would be skipped as stale on the
        # later ones — testing the staleness rule instead of the escalation rule.
        subscriber = Subscriber(
            **{
                **subscriber.__dict__,
                "state": VesselState(
                    lat=PALK_BAY_LAT,
                    lon=PALK_BAY_LON,
                    reported_at=at - timedelta(minutes=2),
                    heading_deg=120.0,
                    speed_knots=3.0,
                ),
            }
        )
        return sched.tick(
            [subscriber],
            picture(
                conditions={
                    subscriber.user_id: conditions(
                        wave_m=wave_m, age=timedelta(minutes=10), relative_to=at
                    )
                }
            ),
            now=at,
            run_id=f"tick-{at.isoformat()}",
            contexts={subscriber.user_id: CONTEXT},
        )

    def wave_record(self, audit):
        return next(
            r for r in audit.records if r.trigger_result.trigger_id is TriggerId.WAVE_HEIGHT
        )

    def test_a_repeat_tick_suppresses(self, subscriber: Subscriber) -> None:
        """The scheduler runs every few minutes; conditions change slowly."""
        sched = scheduler()
        self.run_tick(sched, subscriber, 2.8, NOW)

        second = self.run_tick(sched, subscriber, 2.8, NOW + timedelta(minutes=5))

        assert self.wave_record(second).decision.kind is DecisionKind.SUPPRESS_DUPLICATE
        assert second.sent == ()

    def test_worsening_conditions_escalate_through_suppression(
        self, subscriber: Subscriber
    ) -> None:
        """The branch that must never be optimised away."""
        sched = scheduler()
        self.run_tick(sched, subscriber, 1.6, NOW)  # watch

        second = self.run_tick(sched, subscriber, 2.8, NOW + timedelta(minutes=2))  # warning
        record = self.wave_record(second)

        assert record.decision.kind is DecisionKind.SEND_ESCALATION
        assert record.alert is not None
        assert record.alert.msg_type is CapMsgType.UPDATE

    def test_an_escalation_references_the_alert_it_supersedes(
        self, subscriber: Subscriber
    ) -> None:
        sched = scheduler()
        first = self.run_tick(sched, subscriber, 1.6, NOW)
        first_id = self.wave_record(first).alert.identifier  # type: ignore[union-attr]

        second = self.run_tick(sched, subscriber, 2.8, NOW + timedelta(minutes=2))
        record = self.wave_record(second)

        assert record.alert is not None
        assert record.alert.references == (first_id,)

    def test_conditions_returning_to_calm_cancel_a_severe_alert(
        self, subscriber: Subscriber
    ) -> None:
        """Silence and "it's over" must be distinguishable to someone at sea."""
        sched = scheduler()
        self.run_tick(sched, subscriber, 2.8, NOW)

        later = self.run_tick(sched, subscriber, 0.8, NOW + timedelta(hours=2))
        record = self.wave_record(later)

        assert record.decision.kind is DecisionKind.SEND_CANCEL
        assert record.alert is not None
        assert record.alert.msg_type is CapMsgType.CANCEL

    def test_a_cancellation_retires_the_key_so_the_next_event_is_fresh(
        self, subscriber: Subscriber
    ) -> None:
        sched = scheduler()
        self.run_tick(sched, subscriber, 2.8, NOW)
        self.run_tick(sched, subscriber, 0.8, NOW + timedelta(hours=2))

        again = self.run_tick(sched, subscriber, 2.8, NOW + timedelta(hours=3))

        assert self.wave_record(again).decision.kind is DecisionKind.SEND_NEW


class TestDeliveryAndAudit:
    def test_alerts_are_delivered_and_recorded(self, subscriber: Subscriber) -> None:
        inbox = InAppChannel()
        audit = scheduler(router=DeliveryRouter([inbox])).tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )

        assert len(audit.delivered) == len(audit.sent) > 0
        assert len(inbox.delivered) == len(audit.sent)

    def test_an_undelivered_alert_is_visible_in_the_audit(
        self, subscriber: Subscriber, vallam: VesselProfile
    ) -> None:
        """Sent and delivered are different facts; the audit must not conflate them."""
        from orca_alerts import FcmChannel

        unreachable = Subscriber(
            **{
                **subscriber.__dict__,
                "endpoints": (ChannelEndpoint(DeliveryChannelKind.FCM, "dead-token"),),
            }
        )

        audit = scheduler(router=DeliveryRouter([FcmChannel()])).tick(
            [unreachable], rough_picture(), now=NOW, run_id="tick-1",
            contexts={unreachable.user_id: CONTEXT},
        )

        assert len(audit.sent) > 0
        assert len(audit.delivered) == 0
        assert len(audit.undelivered) == len(audit.sent)
        assert "not delivered" in audit.undelivered[0].notes[0]

    def test_every_record_carries_a_provenance_graph(self, subscriber: Subscriber) -> None:
        """PLAN.md 8.6: every alert stored with its provenance and the thresholds that fired."""
        audit = scheduler().tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )

        assert all(r.provenance is not None for r in audit.records)

    def test_suppressed_records_carry_provenance_too(self, subscriber: Subscriber) -> None:
        sched = scheduler()
        sched.tick(
            [subscriber], rough_picture(), now=NOW, run_id="t1",
            contexts={subscriber.user_id: CONTEXT},
        )

        second = sched.tick(
            [subscriber], rough_picture(), now=NOW + timedelta(minutes=3), run_id="t2",
            contexts={subscriber.user_id: CONTEXT},
        )

        suppressed = [r for r in second.records if r.suppressed]
        assert suppressed
        assert all(r.provenance is not None for r in suppressed)


class TestAudioAttachment:
    def test_an_alert_is_spoken_when_a_pre_generated_clip_exists(
        self, subscriber: Subscriber
    ) -> None:
        """Phase 7.9 through Phase 8.3, with every live voice provider down."""
        from orca_speech import AudioCacheKey, select_voice

        from orca_alerts.messages import build_message
        from orca_alerts.triggers import check_wave_height

        fired = check_wave_height(conditions(wave_m=2.8), subscriber.vessel, now=NOW)
        message = build_message(fired, Language.TAMIL, CONTEXT)
        voice = select_voice(Language.TAMIL)
        cache = InMemoryAudioCache(
            {
                AudioCacheKey.for_text(
                    message.spoken.text,
                    language=Language.TAMIL,
                    voice=voice.voice_id,
                    speed=1.0,
                ): b"pregenerated"
            }
        )

        inbox = InAppChannel()
        audit = scheduler(
            router=DeliveryRouter([inbox]), tts_chain=build_default_chain(cache=cache)
        ).tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )

        wave = next(r for r in audit.sent if r.trigger_result.trigger_id is TriggerId.WAVE_HEIGHT)
        _, delivered_payload = inbox.delivered[0]

        assert wave.was_delivered
        assert delivered_payload.audio_bytes == b"pregenerated"
        assert delivered_payload.has_audio

    def test_a_warning_still_goes_out_when_no_voice_is_available(
        self, subscriber: Subscriber
    ) -> None:
        """A cyclone alert is not worth withholding because a voice service is down."""
        inbox = InAppChannel()
        audit = scheduler(
            router=DeliveryRouter([inbox]),
            tts_chain=build_default_chain(cache=InMemoryAudioCache()),
        ).tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )

        wave = next(r for r in audit.sent if r.trigger_result.trigger_id is TriggerId.WAVE_HEIGHT)
        _, delivered_payload = inbox.delivered[0]

        assert wave.was_delivered
        assert not delivered_payload.has_audio
        assert any("without audio" in note for note in wave.notes)

    def test_text_only_delivery_needs_no_tts_chain_at_all(
        self, subscriber: Subscriber
    ) -> None:
        inbox = InAppChannel()
        scheduler(router=DeliveryRouter([inbox])).tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: CONTEXT},
        )

        assert inbox.delivered
        assert not inbox.delivered[0][1].has_audio


class TestMissingContext:
    def test_an_unfillable_alert_is_recorded_as_a_failure_not_sent(
        self, subscriber: Subscriber
    ) -> None:
        """An alert reading "wave height —" looks delivered. A logged failure does not."""
        audit = scheduler(router=DeliveryRouter([InAppChannel()])).tick(
            [subscriber], rough_picture(), now=NOW, run_id="tick-1",
            contexts={subscriber.user_id: AlertContext()},
        )

        unbuilt = [r for r in audit.records if r.notes and "not built" in r.notes[0]]
        assert unbuilt
        assert all(r.alert is None for r in unbuilt)
        assert all(not r.was_sent for r in unbuilt)
