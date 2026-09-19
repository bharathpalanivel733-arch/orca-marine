"""Delivery channels and routing (PLAN.md Phase 8.3).

Two behaviours matter more than the transports themselves.

**The router tries every channel, not the first that works.** A proactive warning is not a
request/response: there is no way to know which device the fisherman is looking at, and a
duplicate notification costs nothing against a missed cyclone warning. The speech fallback
chain stops at the first success because it is producing one clip; this does not, because
it is trying to reach a person.

**No live provider exists here.** No FCM service account, no VAPID key. Every transport
reports itself unconfigured, which is the branch under test — and the branch a demo machine
will actually run.
"""

from __future__ import annotations

import pytest
from _alert_fixtures import NOW, VesselState
from orca_kernels import VesselProfile
from orca_speech import Language

from orca_alerts import (
    MAX_PUSH_PAYLOAD_BYTES,
    AlertPayload,
    CapSeverity,
    ChannelEndpoint,
    ChannelUnavailableError,
    DeliveryChannelKind,
    DeliveryOutcome,
    DeliveryRouter,
    FcmChannel,
    InAppChannel,
    SmsChannel,
    Subscriber,
    WebPushChannel,
    WebSocketChannel,
    handoff_for,
    payload_from_alert,
)


def payload(severity: CapSeverity = CapSeverity.SEVERE, **overrides: object) -> AlertPayload:
    params: dict[str, object] = {
        "alert_identifier": "orca.a.1",
        "cap_xml": "<alert/>",
        "headline": "High waves near Rameswaram",
        "body": "Wave height 2.8 m, above the 2.0 m limit for your boat.",
        "spoken_text": "Wave height 2.8 metres, above the 2.0 metre limit for your boat.",
        "language": "ta",
        "severity": severity,
    }
    params.update(overrides)
    return AlertPayload(**params)  # type: ignore[arg-type]


def subscriber_with(
    vessel: VesselProfile, *endpoints: ChannelEndpoint
) -> Subscriber:
    return Subscriber(
        user_id="u-rmd-1",
        vessel=vessel,
        language=Language.TAMIL,
        state=VesselState(lat=9.2, lon=79.35, reported_at=NOW),
        endpoints=endpoints,
    )


class Socket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def send_json(self, message: dict[str, object]) -> None:
        self.messages.append(message)


class Response:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class Transport:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> Response:
        self.calls.append((url, kwargs))
        return Response(self.status_code)


class TestPayload:
    def test_push_data_omits_the_cap_xml(self) -> None:
        """CAP XML is far past the 4 KB push budget; the client fetches it by id."""
        data = payload().as_push_data()

        assert "cap_xml" not in data
        assert data["alert_id"] == "orca.a.1"

    def test_push_data_carries_both_readings_of_the_warning(self) -> None:
        """A client that cannot play audio still shows the same words."""
        data = payload().as_push_data()

        assert data["body"]
        assert data["spoken_text"]
        assert data["lang"] == "ta"

    def test_audio_is_referenced_by_cache_key_not_inlined_by_default(self) -> None:
        data = payload(audio_cache_key="ta/ta_female_1/1_00/abc.wav").as_push_data()

        assert data["audio_cache_key"] == "ta/ta_female_1/1_00/abc.wav"
        assert "audio_base64" not in data

    def test_audio_can_be_inlined_when_asked(self) -> None:
        data = payload(audio_bytes=b"RIFF").as_push_data(inline_audio=True)

        assert data["audio_base64"]

    def test_an_offshore_handoff_notice_travels_with_the_alert(self) -> None:
        """The recipient needs to know ORCA may not reach them next time."""
        data = payload(handoff=handoff_for(45.0)).as_push_data()

        assert "GEMINI" in str(data["handoff"])

    def test_a_nearshore_handoff_is_not_shown(self) -> None:
        """Telling someone in coverage about satellite services is noise."""
        data = payload(handoff=handoff_for(3.0)).as_push_data()

        assert "handoff" not in data


class TestInAppChannel:
    def test_it_is_always_available(self) -> None:
        """A database write, not a network call — the floor of the delivery guarantee."""
        assert InAppChannel().available()

    def test_it_records_the_alert(self, vallam: VesselProfile) -> None:
        channel = InAppChannel()
        router = DeliveryRouter([channel])

        router.deliver(
            subscriber_with(vallam, ChannelEndpoint(DeliveryChannelKind.IN_APP, "u-rmd-1")),
            payload(),
        )

        assert len(channel.delivered) == 1


class TestWebSocketChannel:
    def test_it_delivers_to_an_open_socket(self, vallam: VesselProfile) -> None:
        socket = Socket()
        router = DeliveryRouter([WebSocketChannel({"u-rmd-1": socket})])

        report = router.deliver(
            subscriber_with(vallam, ChannelEndpoint(DeliveryChannelKind.WEBSOCKET, "u-rmd-1")),
            payload(),
        )

        assert report.delivered
        assert socket.messages[0]["type"] == "alert"

    def test_a_closed_socket_is_recorded_not_raised(self, vallam: VesselProfile) -> None:
        """The app being shut is the normal case at sea, not an error."""
        router = DeliveryRouter([WebSocketChannel({})])

        report = router.deliver(
            subscriber_with(vallam, ChannelEndpoint(DeliveryChannelKind.WEBSOCKET, "u-rmd-1")),
            payload(),
        )

        assert not report.delivered
        assert report.attempts[0].outcome is DeliveryOutcome.NO_ENDPOINT


class TestWebPush:
    def test_it_is_unconfigured_without_vapid_keys(self) -> None:
        assert not WebPushChannel().available()

    def test_an_unconfigured_channel_declines_rather_than_crashing(self) -> None:
        with pytest.raises(ChannelUnavailableError):
            WebPushChannel().send(
                ChannelEndpoint(DeliveryChannelKind.WEB_PUSH, "https://push.invalid/x"), payload()
            )

    def test_urgency_headers_track_severity(self) -> None:
        """A low-urgency push may be held until the device wakes — wrong for a cyclone."""
        channel = WebPushChannel()

        assert channel.build_headers(payload(CapSeverity.EXTREME))["Urgency"] == "high"
        assert channel.build_headers(payload(CapSeverity.MINOR))["Urgency"] == "low"

    def test_extreme_alerts_get_a_shorter_ttl(self) -> None:
        """An hour-old cyclone warning delivered late is worse than none."""
        channel = WebPushChannel()

        assert channel.build_headers(payload(CapSeverity.EXTREME))["TTL"] == "600"
        assert channel.build_headers(payload(CapSeverity.SEVERE))["TTL"] == "3600"

    def test_an_expired_subscription_is_reported_distinctly(self, vallam: VesselProfile) -> None:
        """A dead token must retire the endpoint, not look like a transient failure."""
        channel = WebPushChannel(
            vapid_private_key="k", vapid_subject="mailto:x@y.z", transport=Transport(410)
        )
        router = DeliveryRouter([channel])

        report = router.deliver(
            subscriber_with(
                vallam, ChannelEndpoint(DeliveryChannelKind.WEB_PUSH, "https://push.invalid/x")
            ),
            payload(),
        )

        assert report.attempts[0].outcome is DeliveryOutcome.EXPIRED_TOKEN
        assert report.expired_endpoints == ("https://push.invalid/x",)


class TestFcm:
    def test_it_is_unconfigured_without_a_service_account(self) -> None:
        assert not FcmChannel().available()

    def test_the_message_is_data_only(self) -> None:
        """A notification block lets the OS render before the app can pick a language."""
        message = FcmChannel().build_message(
            ChannelEndpoint(DeliveryChannelKind.FCM, "token"), payload()
        )

        assert "notification" not in message["message"]
        assert message["message"]["data"]["lang"] == "ta"

    def test_priority_tracks_severity(self) -> None:
        channel = FcmChannel()

        extreme = channel.build_message(
            ChannelEndpoint(DeliveryChannelKind.FCM, "t"), payload(CapSeverity.EXTREME)
        )
        minor = channel.build_message(
            ChannelEndpoint(DeliveryChannelKind.FCM, "t"), payload(CapSeverity.MINOR)
        )

        assert extreme["message"]["android"]["priority"] == "high"
        assert minor["message"]["android"]["priority"] == "normal"

    def test_an_oversized_payload_is_refused_at_build_time(self) -> None:
        """Otherwise FCM rejects it at delivery, when it is too late to shorten anything."""
        huge = payload(body="x" * (MAX_PUSH_PAYLOAD_BYTES + 100))

        with pytest.raises(ValueError, match="above the"):
            FcmChannel().build_message(ChannelEndpoint(DeliveryChannelKind.FCM, "t"), huge)


class TestSmsPolicy:
    def test_sms_is_skipped_for_low_severity(self, vallam: VesselProfile) -> None:
        """Charged per message and carries no audio; reserved for where it is worth it."""
        router = DeliveryRouter([SmsChannel()])

        report = router.deliver(
            subscriber_with(vallam, ChannelEndpoint(DeliveryChannelKind.SMS, "+910000000000")),
            payload(CapSeverity.MODERATE),
        )

        assert report.attempts[0].outcome is DeliveryOutcome.SKIPPED_POLICY

    def test_a_policy_skip_is_a_decision_not_a_failure(self, vallam: VesselProfile) -> None:
        """Recorded distinctly so it never reads as an outage in the operations view."""
        router = DeliveryRouter([SmsChannel()])

        report = router.deliver(
            subscriber_with(vallam, ChannelEndpoint(DeliveryChannelKind.SMS, "+910000000000")),
            payload(CapSeverity.MINOR),
        )

        assert report.attempts[0].outcome is not DeliveryOutcome.ERROR

    def test_sms_is_attempted_for_severe_alerts(self, vallam: VesselProfile) -> None:
        router = DeliveryRouter([SmsChannel()])

        report = router.deliver(
            subscriber_with(vallam, ChannelEndpoint(DeliveryChannelKind.SMS, "+910000000000")),
            payload(CapSeverity.SEVERE),
        )

        assert report.attempts[0].outcome is DeliveryOutcome.UNCONFIGURED


class TestRouting:
    def test_every_channel_is_tried_not_just_the_first(self, vallam: VesselProfile) -> None:
        """There is no way to know which device the fisherman is looking at."""
        inbox = InAppChannel()
        socket = Socket()
        router = DeliveryRouter([inbox, WebSocketChannel({"u-rmd-1": socket})])

        report = router.deliver(
            subscriber_with(
                vallam,
                ChannelEndpoint(DeliveryChannelKind.IN_APP, "u-rmd-1"),
                ChannelEndpoint(DeliveryChannelKind.WEBSOCKET, "u-rmd-1"),
            ),
            payload(),
        )

        assert len(report.delivered_channels) == 2
        assert len(inbox.delivered) == 1
        assert len(socket.messages) == 1

    def test_one_failing_channel_does_not_stop_the_others(self, vallam: VesselProfile) -> None:
        inbox = InAppChannel()
        router = DeliveryRouter([WebPushChannel(), inbox])

        report = router.deliver(
            subscriber_with(
                vallam,
                ChannelEndpoint(DeliveryChannelKind.WEB_PUSH, "https://push.invalid/x"),
                ChannelEndpoint(DeliveryChannelKind.IN_APP, "u-rmd-1"),
            ),
            payload(),
        )

        assert report.delivered
        assert report.delivered_channels == (DeliveryChannelKind.IN_APP,)

    def test_an_unexpected_exception_is_recorded_not_propagated(
        self, vallam: VesselProfile
    ) -> None:
        """One bad channel must not abandon a cyclone warning to everyone else."""

        class Exploding:
            kind = DeliveryChannelKind.IN_APP

            def available(self) -> bool:
                return True

            def send(self, endpoint: ChannelEndpoint, alert_payload: AlertPayload) -> str:
                raise RuntimeError("socket reset")

        router = DeliveryRouter([Exploding()])

        report = router.deliver(
            subscriber_with(vallam, ChannelEndpoint(DeliveryChannelKind.IN_APP, "u-rmd-1")),
            payload(),
        )

        assert report.attempts[0].outcome is DeliveryOutcome.ERROR
        assert "socket reset" in (report.attempts[0].detail or "")

    def test_an_inactive_endpoint_is_not_attempted(self, vallam: VesselProfile) -> None:
        """A retired handset must stop counting as a successful delivery."""
        inbox = InAppChannel()
        router = DeliveryRouter([inbox])

        report = router.deliver(
            subscriber_with(
                vallam, ChannelEndpoint(DeliveryChannelKind.IN_APP, "old-device", active=False)
            ),
            payload(),
        )

        assert report.attempts == ()
        assert not report.delivered

    def test_an_endpoint_with_no_registered_channel_is_recorded(
        self, vallam: VesselProfile
    ) -> None:
        router = DeliveryRouter([InAppChannel()])

        report = router.deliver(
            subscriber_with(vallam, ChannelEndpoint(DeliveryChannelKind.FCM, "token")),
            payload(),
        )

        assert report.attempts[0].outcome is DeliveryOutcome.UNCONFIGURED

    def test_a_router_needs_at_least_one_channel(self) -> None:
        with pytest.raises(ValueError, match="at least one channel"):
            DeliveryRouter([])

    def test_nothing_configured_means_nothing_delivered_and_it_says_so(
        self, vallam: VesselProfile
    ) -> None:
        """The state a demo machine is actually in."""
        router = DeliveryRouter([WebPushChannel(), FcmChannel()])

        report = router.deliver(
            subscriber_with(
                vallam,
                ChannelEndpoint(DeliveryChannelKind.WEB_PUSH, "https://push.invalid/x"),
                ChannelEndpoint(DeliveryChannelKind.FCM, "token"),
            ),
            payload(),
        )

        assert not report.delivered
        assert all(a.outcome is DeliveryOutcome.UNCONFIGURED for a in report.attempts)


def test_a_payload_can_be_built_straight_from_a_cap_alert() -> None:
    from test_cap import alert

    built = payload_from_alert(
        alert(), body="Wave height 2.8 m.", spoken_text="Wave height 2.8 metres."
    )

    assert built.alert_identifier == "orca.vessel.wave_height.abc123"
    assert built.severity is CapSeverity.SEVERE
    assert built.cap_xml.startswith("<?xml")
