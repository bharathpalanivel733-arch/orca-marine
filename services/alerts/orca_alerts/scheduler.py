"""The scheduled evaluation tick (PLAN.md Phase 8.1).

One function does the work — :meth:`AlertScheduler.tick` — and it is a **pure function of
its inputs plus an explicit clock**. There is no Celery import, no ambient `datetime.now()`,
and no hidden state beyond the deduplicator that is passed in.

That is a deliberate choice about where the difficulty lives. Running a function every five
minutes is solved: Celery beat, Cloud Scheduler, a cron entry, an APScheduler job — any of
them works, and which one is a deployment decision. What is *not* solved by any of them is
whether the evaluation produces the right alerts, suppresses the right repeats, and records
why. Binding that logic to a scheduler framework would make it testable only by running
one, and this is the part of ORCA that most needs to be verifiable at rest.

So the seam is: a scheduler framework calls ``tick(now=...)`` on a cadence, and everything
that could be wrong is inside a function that takes a time and returns a report.

Order of evaluation per subscriber is fixed — cyclone, then vessel-relative, then geofence —
so two runs over the same inputs produce the same records in the same order. That is what
makes the audit diffable and the deduplication stable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from orca_geo import DriftPredictor, ImblGeofence, VesselMotion
from orca_speech import (
    AllProvidersFailedError,
    Language,
    SynthesisRequest,
    TtsChain,
    select_voice,
)

from orca_alerts.audit import AlertAuditRecord, TickAudit, build_alert_provenance
from orca_alerts.cap import (
    CATEGORY_BY_TRIGGER,
    RESPONSE_BY_TRIGGER,
    CapAlert,
    CapArea,
    CapInfo,
    CapMsgType,
    CapScope,
    CapStatus,
    certainty_for,
    make_identifier,
    severity_for,
    urgency_for,
)
from orca_alerts.conditions import HazardPicture
from orca_alerts.dedup import (
    AlertDeduplicator,
    AlertKey,
    Decision,
    DecisionKind,
    hazard_ref_for,
)
from orca_alerts.delivery import AlertPayload, DeliveryReport, DeliveryRouter
from orca_alerts.handoff import HandoffNotice, handoff_for
from orca_alerts.messages import (
    AlertContext,
    AlertMessage,
    MissingAlertContextError,
    build_message,
    event_name,
)
from orca_alerts.subscriber import Subscriber, classify_skip
from orca_alerts.triggers import (
    TriggerId,
    TriggerResult,
    check_geofence_proximity,
    check_predictive_drift,
    evaluate_cyclones,
    evaluate_vessel_conditions,
)

# How long an alert stays valid if nothing supersedes it. Matched to the tick cadence times
# a small factor: an alert that expired before the next evaluation would leave a gap, and
# one that outlived several ticks would still be showing after conditions had changed.
DEFAULT_ALERT_LIFETIME = timedelta(hours=1)

# Which dataset each rule is attributed to in provenance.
DATASET_BY_TRIGGER: dict[TriggerId, str] = {
    TriggerId.CYCLONE_WIND: "imd_cyclone_wind",
    TriggerId.CYCLONE_CONE: "imd_cyclone_cou",
    TriggerId.WAVE_HEIGHT: "incois_osf",
    TriggerId.WIND_SPEED: "incois_osf",
    TriggerId.LIGHTNING: "imd_nowcast",
    TriggerId.SQUALL: "imd_nowcast",
    TriggerId.GEOFENCE_PROXIMITY: "imbl_1974_1976",
    TriggerId.GEOFENCE_DRIFT: "imbl_1974_1976",
}


@dataclass(frozen=True)
class TickConfig:
    """Knobs for one tick, all with defensible defaults."""

    languages: tuple[Language, ...] = (Language.ENGLISH,)
    alert_lifetime: timedelta = DEFAULT_ALERT_LIFETIME
    drift_horizon: timedelta = timedelta(hours=1)
    # Radius of the CAP <circle> drawn around a vessel. Not a claim about the hazard's
    # extent — it is the area the alert is *about*, which is this boat and its immediate
    # surroundings.
    alert_radius_km: float = 5.0
    sender: str = "orca@ocean-iq.in"


class AlertScheduler:
    """Evaluates every subscriber against the current hazard picture."""

    def __init__(
        self,
        *,
        deduplicator: AlertDeduplicator | None = None,
        router: DeliveryRouter | None = None,
        geofence: ImblGeofence | None = None,
        tts_chain: TtsChain | None = None,
        config: TickConfig | None = None,
    ) -> None:
        self._dedup = deduplicator or AlertDeduplicator()
        self._router = router
        self._geofence = geofence
        # Optional: alert audio is attached when a chain is supplied (PLAN.md 7.9, 8.3).
        # Without one, alerts are text-only and the audit records that.
        self._tts_chain = tts_chain
        self._config = config or TickConfig()

    @property
    def deduplicator(self) -> AlertDeduplicator:
        return self._dedup

    def tick(
        self,
        subscribers: Sequence[Subscriber],
        picture: HazardPicture,
        *,
        now: datetime,
        run_id: str,
        contexts: dict[str, AlertContext] | None = None,
    ) -> TickAudit:
        """Evaluate everyone once. Returns the full audit for this run."""
        records: list[AlertAuditRecord] = []
        skipped: list[tuple[str, str]] = []
        evaluated = 0

        for subscriber in subscribers:
            skip = classify_skip(subscriber, now)
            if skip is not None:
                skipped.append((skip.user_id, skip.reason.value))
                continue

            evaluated += 1
            context = (contexts or {}).get(subscriber.user_id, AlertContext())
            for result in self._evaluate(subscriber, picture, now=now):
                record = self._handle(
                    subscriber, result, context, now=now, run_id=run_id
                )
                records.append(record)

        return TickAudit(
            run_id=run_id,
            started_at=now,
            records=tuple(records),
            skipped=tuple(skipped),
            subscribers_evaluated=evaluated,
        )

    def _evaluate(
        self, subscriber: Subscriber, picture: HazardPicture, *, now: datetime
    ) -> tuple[TriggerResult, ...]:
        """Every rule for one subscriber, in a fixed order."""
        assert subscriber.state is not None  # guaranteed by classify_skip
        state = subscriber.state
        results: list[TriggerResult] = []

        # (a) Cyclone containment.
        results.extend(evaluate_cyclones(picture, state.lat, state.lon, now=now))

        # (b) Vessel-relative thresholds.
        conditions = picture.conditions_for(subscriber.user_id)
        if conditions is not None:
            results.extend(
                evaluate_vessel_conditions(conditions, subscriber.vessel, now=now)
            )

        # (c) Geofence proximity and predictive drift.
        if self._geofence is not None:
            proximity = self._geofence.proximity(state.lat, state.lon)
            results.append(check_geofence_proximity(proximity))

            motion = VesselMotion.from_knots(
                state.heading_deg,
                state.speed_knots,
                current_east_ms=conditions.current_east_ms if conditions else 0.0,
                current_north_ms=conditions.current_north_ms if conditions else 0.0,
            )
            forecast = DriftPredictor(self._geofence).project(
                state.lat, state.lon, motion, horizon=self._config.drift_horizon
            )
            results.append(check_predictive_drift(forecast))

        return tuple(results)

    def _handle(
        self,
        subscriber: Subscriber,
        result: TriggerResult,
        context: AlertContext,
        *,
        now: datetime,
        run_id: str,
    ) -> AlertAuditRecord:
        """Decide, build, deliver and record one rule's outcome.

        Every path produces a record, including the suppressed and unevaluated ones — that
        is what makes the audit able to answer why somebody was *not* warned.
        """
        key = AlertKey(subscriber.user_id, result.trigger_id, hazard_ref_for(result))
        severity = severity_for(result)
        decision = self._dedup.decide(key, result, severity, now=now)

        provenance = build_alert_provenance(
            run_id=f"{run_id}:{key}",
            user_id=subscriber.user_id,
            result=result,
            evaluated_at=now,
            dataset_id=DATASET_BY_TRIGGER[result.trigger_id],
        )

        if not decision.should_send:
            return AlertAuditRecord(
                run_id=run_id,
                user_id=subscriber.user_id,
                evaluated_at=now,
                trigger_result=result,
                decision=decision,
                provenance=provenance,
            )

        notes: list[str] = []
        try:
            alert, message = self._build_alert(
                subscriber, result, context, decision_now=now, decision=decision
            )
        except MissingAlertContextError as exc:
            # The rule fired but the sentence cannot be filled. Recorded as a real failure
            # rather than sent with a gap: an alert reading "wave height —" looks delivered.
            return AlertAuditRecord(
                run_id=run_id,
                user_id=subscriber.user_id,
                evaluated_at=now,
                trigger_result=result,
                decision=decision,
                provenance=provenance,
                notes=(f"alert not built: {exc}",),
            )

        delivery: DeliveryReport | None = None
        if self._router is not None:
            payload, audio_note = self._payload_for(subscriber, alert, message, result)
            if audio_note:
                notes.append(audio_note)
            delivery = self._router.deliver(subscriber, payload)
            if not delivery.delivered:
                notes.append("emitted but not delivered on any channel")

        # Committed after the send attempt, not before: recording a suppression for an alert
        # that never went out would silence the next tick as well.
        self._dedup.commit(
            key, identifier=alert.identifier, severity=severity, result=result, now=now
        )
        if decision.kind is DecisionKind.SEND_CANCEL:
            # A cancellation closes the episode; the next occurrence is a fresh alert, not
            # a repeat of the one just stood down.
            self._dedup.retire(key)

        return AlertAuditRecord(
            run_id=run_id,
            user_id=subscriber.user_id,
            evaluated_at=now,
            trigger_result=result,
            decision=decision,
            alert=alert,
            delivery=delivery,
            provenance=provenance,
            notes=tuple(notes),
        )

    def _build_alert(
        self,
        subscriber: Subscriber,
        result: TriggerResult,
        context: AlertContext,
        *,
        decision_now: datetime,
        decision: Decision,
    ) -> tuple[CapAlert, AlertMessage]:
        """Assemble the CAP alert, one ``<info>`` block per configured language.

        Returns the message for the subscriber's own language alongside it, because that is
        what gets spoken — and taking it from here rather than re-rendering guarantees the
        audio and the CAP text are the same sentence.
        """
        assert subscriber.state is not None

        area = CapArea(
            description=context.location_name or f"Vessel {subscriber.vessel.vessel_id}",
            circle_lat=subscriber.state.lat,
            circle_lon=subscriber.state.lon,
            circle_radius_km=self._config.alert_radius_km,
        )

        infos: list[CapInfo] = []
        messages: list[AlertMessage] = []
        for language in self._languages_for(subscriber):
            message = build_message(result, language, context)
            messages.append(message)
            infos.append(
                CapInfo(
                    language=language,
                    category=CATEGORY_BY_TRIGGER[result.trigger_id],
                    event=event_name(result),
                    urgency=urgency_for(result),
                    severity=severity_for(result),
                    certainty=certainty_for(result),
                    headline=message.headline,
                    description=message.text,
                    instruction=message.text,
                    response_type=RESPONSE_BY_TRIGGER[result.trigger_id],
                    effective=decision_now,
                    expires=decision_now + self._config.alert_lifetime,
                    source=context.authority,
                    parameters=(
                        ("trigger_id", result.trigger_id.value),
                        ("outcome", result.outcome.value),
                        ("threshold_source", result.threshold_source),
                        ("observed", str(result.observed)),
                        ("threshold", str(result.threshold)),
                    ),
                    areas=(area,),
                )
            )

        msg_type = {
            DecisionKind.SEND_NEW: CapMsgType.ALERT,
            DecisionKind.SEND_ESCALATION: CapMsgType.UPDATE,
            DecisionKind.SEND_UPDATE: CapMsgType.UPDATE,
            DecisionKind.SEND_CANCEL: CapMsgType.CANCEL,
        }[decision.kind]

        references = (
            (decision.previous.identifier,)
            if decision.references_previous and decision.previous is not None
            else ()
        )

        alert = CapAlert(
            identifier=make_identifier(
                user_id=subscriber.user_id, trigger_id=result.trigger_id, sent=decision_now
            ),
            sender=self._config.sender,
            sent=decision_now,
            status=CapStatus.ACTUAL,
            msg_type=msg_type,
            # Private: this alert is about one vessel's position, which is not public
            # information. A fleet or region warning uses Public scope (see workflows).
            scope=CapScope.PRIVATE,
            addresses=(subscriber.user_id,),
            info=tuple(infos),
            references=references,
        )
        return alert, messages[0]

    def _languages_for(self, subscriber: Subscriber) -> tuple[Language, ...]:
        """The subscriber's language first, then any others configured.

        Theirs first because CAP consumers that read only the first ``<info>`` block should
        get the one the recipient can actually read.
        """
        extras = tuple(
            lang for lang in self._config.languages if lang is not subscriber.language
        )
        return (subscriber.language, *extras)

    def _payload_for(
        self,
        subscriber: Subscriber,
        alert: CapAlert,
        message: AlertMessage,
        result: TriggerResult,
    ) -> tuple[AlertPayload, str]:
        """Build the transport payload: text, audio and the hand-off notice.

        Returns the payload and a note about the audio, so a warning that went out without a
        voice is recorded as such rather than looking complete in the audit.
        """
        info = alert.info[0]
        handoff: HandoffNotice | None = None
        distance = result.observed.get("distance_from_shore_km")
        if isinstance(distance, int | float):
            handoff = handoff_for(float(distance))

        audio_bytes: bytes | None = None
        audio_key: str | None = None
        note = ""
        if self._tts_chain is not None:
            try:
                voice = select_voice(message.language, subscriber.voice_gender)
                request = SynthesisRequest(
                    text=message.spoken.text, language=message.language, voice=voice
                )
                synthesis = self._tts_chain.synthesize(request)
                audio_bytes = synthesis.audio
                audio_key = synthesis.cache_key.object_key
            except (AllProvidersFailedError, LookupError, ValueError) as exc:
                # The warning still goes out. A cyclone alert is not worth withholding
                # because a voice service is down — but the audit says it was silent.
                note = f"alert sent without audio: {type(exc).__name__}: {exc}"

        return (
            AlertPayload(
                alert_identifier=alert.identifier,
                cap_xml=alert.to_xml(),
                headline=info.headline,
                body=info.description,
                # The *normalized* text — units expanded, numbers verified unchanged — so
                # the spoken warning and the CAP description carry identical figures.
                spoken_text=message.spoken.text,
                language=info.language.value,
                severity=info.severity,
                audio_cache_key=audio_key,
                audio_bytes=audio_bytes,
                handoff=handoff,
                sent_at=alert.sent,
            ),
            note,
        )
