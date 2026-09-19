"""Delivery channels and routing (PLAN.md Phase 8.3).

Four transports, one interface, and an attempt log — the same shape as the speech fallback
chain, for the same reason: a warning that went out by a degraded path must be
distinguishable from one that went out cleanly, and "delivery failed" is useless without
saying which channel and why.

The channels differ in a way that is easy to gloss over and important to state:

* **In-app and WebSocket** reach a phone with the app open. Immediate, and useless to
  someone whose screen is off — which is most of the time at sea.
* **Web Push and FCM** wake a backgrounded phone. This is the channel that actually matters
  for a proactive alert, and the one whose token expires silently.
* **SMS** survives a data connection too poor for anything else, but carries no audio and
  costs money per message, so it is reserved for high severity.

None of them reaches a vessel beyond cellular coverage. :mod:`orca_alerts.handoff` says so
plainly, and the router attaches that notice rather than letting a queued message look like
a delivered one.

**Every channel here is an abstraction with no live provider behind it.** There is no FCM
service account and no VAPID key in this repository. Each transport is implemented against
its documented payload shape and reports itself unconfigured, which is exactly the branch
the tests exercise. PROGRESS.md records that rather than implying a working push pipeline.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Protocol, runtime_checkable

from orca_alerts.cap import SEVERITY_ORDER, CapAlert, CapSeverity
from orca_alerts.handoff import HandoffNotice
from orca_alerts.subscriber import ChannelEndpoint, DeliveryChannelKind, Subscriber

# SMS is charged per message and carries no audio, so it is reserved for the severities
# where reaching someone is worth the cost and the loss of the spoken form.
SMS_MIN_SEVERITY = CapSeverity.SEVERE

# Push payload budget. FCM caps a data message at 4 KB, so inlined audio does not fit and
# the payload carries the cache key instead — which the client fetches, or plays from its
# own pre-generated cache if it already has the clip.
MAX_PUSH_PAYLOAD_BYTES = 4096


class DeliveryOutcome(StrEnum):
    """What happened on one channel."""

    DELIVERED = "delivered"
    UNCONFIGURED = "unconfigured"
    NO_ENDPOINT = "no_endpoint"
    REJECTED = "rejected"
    EXPIRED_TOKEN = "expired_token"
    ERROR = "error"
    SKIPPED_POLICY = "skipped_policy"


@dataclass(frozen=True)
class DeliveryAttempt:
    """One channel's attempt at one subscriber."""

    channel: DeliveryChannelKind
    outcome: DeliveryOutcome
    endpoint: str = ""
    detail: str | None = None
    latency_ms: float | None = None

    @property
    def delivered(self) -> bool:
        return self.outcome is DeliveryOutcome.DELIVERED


class ChannelUnavailableError(RuntimeError):
    """A channel declined. Caught by the router and recorded as an attempt."""

    def __init__(
        self, detail: str, outcome: DeliveryOutcome = DeliveryOutcome.UNCONFIGURED
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.outcome = outcome


@dataclass(frozen=True)
class AlertPayload:
    """What is actually sent, independent of transport.

    Carries both readings of the warning — the text and the audio reference — so a client
    that cannot play audio still shows the same words, and a listener and a reader receive
    the same warning.
    """

    alert_identifier: str
    cap_xml: str
    headline: str
    body: str
    spoken_text: str
    language: str
    severity: CapSeverity
    audio_cache_key: str | None = None
    audio_bytes: bytes | None = None
    audio_content_type: str = "audio/wav"
    handoff: HandoffNotice | None = None
    sent_at: datetime | None = None

    def as_push_data(self, *, inline_audio: bool = False) -> dict[str, Any]:
        """The compact form for a push message.

        CAP XML is deliberately excluded: it is far past the 4 KB budget, and the client
        fetches it by identifier when it needs the full record. What travels is what the
        phone must display and speak the moment it wakes.
        """
        data: dict[str, Any] = {
            "alert_id": self.alert_identifier,
            "headline": self.headline,
            "body": self.body,
            "spoken_text": self.spoken_text,
            "lang": self.language,
            "severity": self.severity.value,
        }
        if self.audio_cache_key:
            data["audio_cache_key"] = self.audio_cache_key
        if self.handoff is not None and self.handoff.should_display:
            data["handoff"] = self.handoff.message
        if inline_audio and self.audio_bytes:
            data["audio_base64"] = base64.b64encode(self.audio_bytes).decode("ascii")
        return data

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_bytes or self.audio_cache_key)


@runtime_checkable
class DeliveryChannel(Protocol):
    """One way of getting an alert to a phone."""

    kind: DeliveryChannelKind

    def available(self) -> bool: ...

    def send(self, endpoint: ChannelEndpoint, payload: AlertPayload) -> str: ...


class InAppChannel:
    """Writes the alert to the user's in-app inbox.

    Always available, because it is a database write rather than a network call — which
    makes it the floor of the delivery guarantee: an alert is never lost, even if every push
    transport is down. It just may not be *seen* until the app is next opened.
    """

    kind = DeliveryChannelKind.IN_APP

    def __init__(self, sink: list[tuple[str, AlertPayload]] | None = None) -> None:
        self._sink = sink if sink is not None else []

    def available(self) -> bool:
        return True

    def send(self, endpoint: ChannelEndpoint, payload: AlertPayload) -> str:
        self._sink.append((endpoint.address, payload))
        return f"inbox:{endpoint.address}:{payload.alert_identifier}"

    @property
    def delivered(self) -> tuple[tuple[str, AlertPayload], ...]:
        return tuple(self._sink)


class WebSocketChannel:
    """Pushes over an open WebSocket to a foregrounded app.

    Only useful while a connection is live, so ``available`` reflects whether *this* user has
    one. A registry is injected rather than assumed so the router's behaviour is testable
    without a server.
    """

    kind = DeliveryChannelKind.WEBSOCKET

    def __init__(self, connections: dict[str, Any] | None = None) -> None:
        self._connections = connections if connections is not None else {}

    def available(self) -> bool:
        return bool(self._connections)

    def send(self, endpoint: ChannelEndpoint, payload: AlertPayload) -> str:
        connection = self._connections.get(endpoint.address)
        if connection is None:
            raise ChannelUnavailableError(
                f"no open socket for {endpoint.address}", DeliveryOutcome.NO_ENDPOINT
            )
        connection.send_json({"type": "alert", **payload.as_push_data()})
        return f"ws:{endpoint.address}:{payload.alert_identifier}"


class WebPushChannel:
    """Web Push (VAPID) to a browser push service.

    **No VAPID keys exist in this repository**, so this reports itself unconfigured and the
    router falls through. The payload shape and the urgency header mapping are what can
    honestly be verified offline, and they are.
    """

    kind = DeliveryChannelKind.WEB_PUSH

    # RFC 8030 urgency, mapped from CAP severity. A low-urgency push may be held by the
    # push service until the device next wakes, which is wrong for a cyclone alert.
    URGENCY_BY_SEVERITY: ClassVar[dict[CapSeverity, str]] = {
        CapSeverity.EXTREME: "high",
        CapSeverity.SEVERE: "high",
        CapSeverity.MODERATE: "normal",
        CapSeverity.MINOR: "low",
        CapSeverity.UNKNOWN: "normal",
    }

    def __init__(
        self,
        *,
        vapid_private_key: str | None = None,
        vapid_subject: str | None = None,
        transport: Any | None = None,
    ) -> None:
        self._key = vapid_private_key
        self._subject = vapid_subject
        self._transport = transport

    def available(self) -> bool:
        return bool(self._key and self._subject and self._transport is not None)

    def build_headers(self, payload: AlertPayload) -> dict[str, str]:
        """RFC 8030 headers. Verifiable without a key, unlike the call itself."""
        return {
            "TTL": "600" if payload.severity is CapSeverity.EXTREME else "3600",
            "Urgency": self.URGENCY_BY_SEVERITY[payload.severity],
            "Content-Encoding": "aes128gcm",
        }

    def send(self, endpoint: ChannelEndpoint, payload: AlertPayload) -> str:
        if not self.available():
            raise ChannelUnavailableError("no VAPID keys configured")
        assert self._transport is not None
        response = self._transport.post(
            endpoint.address,
            json=payload.as_push_data(),
            headers=self.build_headers(payload),
        )
        status = getattr(response, "status_code", 200)
        if status in {404, 410}:
            raise ChannelUnavailableError(
                "push subscription has expired", DeliveryOutcome.EXPIRED_TOKEN
            )
        if status >= 400:
            raise ChannelUnavailableError(
                f"push service returned {status}", DeliveryOutcome.REJECTED
            )
        return f"webpush:{payload.alert_identifier}"


class FcmChannel:
    """Firebase Cloud Messaging to an Android handset.

    **No service account exists here**, so this too reports itself unconfigured. The payload
    builder is real and tested: FCM caps a data message at 4 KB, and an alert that silently
    exceeded it would be rejected at delivery time rather than at build time.
    """

    kind = DeliveryChannelKind.FCM

    PRIORITY_BY_SEVERITY: ClassVar[dict[CapSeverity, str]] = {
        CapSeverity.EXTREME: "high",
        CapSeverity.SEVERE: "high",
        CapSeverity.MODERATE: "normal",
        CapSeverity.MINOR: "normal",
        CapSeverity.UNKNOWN: "normal",
    }

    def __init__(
        self,
        *,
        project_id: str | None = None,
        credentials: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self._project_id = project_id
        self._credentials = credentials
        self._transport = transport

    def available(self) -> bool:
        return bool(self._project_id and self._credentials and self._transport is not None)

    def build_message(self, endpoint: ChannelEndpoint, payload: AlertPayload) -> dict[str, Any]:
        """The FCM v1 message body.

        Data-only: a `notification` block would let the OS render the alert before the app
        could choose a language or start the audio, and an alert shown in the wrong language
        is worse than one shown a second later.
        """
        data = {k: str(v) for k, v in payload.as_push_data().items()}
        message = {
            "message": {
                "token": endpoint.address,
                "data": data,
                "android": {
                    "priority": self.PRIORITY_BY_SEVERITY[payload.severity],
                    "ttl": "600s" if payload.severity is CapSeverity.EXTREME else "3600s",
                },
            }
        }
        size = len(str(message).encode("utf-8"))
        if size > MAX_PUSH_PAYLOAD_BYTES:
            msg = f"FCM payload is {size} bytes, above the {MAX_PUSH_PAYLOAD_BYTES} byte limit"
            raise ValueError(msg)
        return message

    def send(self, endpoint: ChannelEndpoint, payload: AlertPayload) -> str:
        if not self.available():
            raise ChannelUnavailableError("no FCM service account configured")
        assert self._transport is not None
        body = self.build_message(endpoint, payload)
        response = self._transport.post(
            f"https://fcm.googleapis.com/v1/projects/{self._project_id}/messages:send",
            json=body,
        )
        status = getattr(response, "status_code", 200)
        if status in {404, 410}:
            raise ChannelUnavailableError(
                "FCM token is no longer valid", DeliveryOutcome.EXPIRED_TOKEN
            )
        if status >= 400:
            raise ChannelUnavailableError(f"FCM returned {status}", DeliveryOutcome.REJECTED)
        return f"fcm:{payload.alert_identifier}"


class SmsChannel:
    """SMS, for when data is too poor for anything else.

    Policy-gated by severity in the router rather than here, so the reason a message was not
    sent is recorded as a decision (``SKIPPED_POLICY``) rather than as a failure.
    """

    kind = DeliveryChannelKind.SMS

    def __init__(self, *, gateway: Any | None = None) -> None:
        self._gateway = gateway

    def available(self) -> bool:
        return self._gateway is not None

    def send(self, endpoint: ChannelEndpoint, payload: AlertPayload) -> str:
        if not self.available():
            raise ChannelUnavailableError("no SMS gateway configured")
        assert self._gateway is not None
        # 160 GSM-7 characters; Indic scripts are UCS-2 at 70. The headline is built to fit.
        self._gateway.send(endpoint.address, payload.headline)
        return f"sms:{endpoint.address}:{payload.alert_identifier}"


@dataclass(frozen=True)
class DeliveryReport:
    """What happened when one alert was delivered to one subscriber."""

    user_id: str
    alert_identifier: str
    attempts: tuple[DeliveryAttempt, ...] = field(default_factory=tuple)

    @property
    def delivered(self) -> bool:
        """Whether the alert reached the subscriber by any channel."""
        return any(a.delivered for a in self.attempts)

    @property
    def delivered_channels(self) -> tuple[DeliveryChannelKind, ...]:
        return tuple(a.channel for a in self.attempts if a.delivered)

    @property
    def expired_endpoints(self) -> tuple[str, ...]:
        """Endpoints a provider reported dead, for retiring from the subscriber record."""
        return tuple(
            a.endpoint for a in self.attempts if a.outcome is DeliveryOutcome.EXPIRED_TOKEN
        )


class DeliveryRouter:
    """Sends one alert over every channel the subscriber has.

    **Every** channel, not the first that works. A proactive warning is not a request/response
    — there is no way to know which device the fisherman is looking at, and the cost of a
    duplicate notification is trivial against the cost of a missed cyclone warning. Speech
    fallback stops at the first success because it is producing one clip; this does not,
    because it is trying to reach a person.
    """

    def __init__(self, channels: Sequence[DeliveryChannel]) -> None:
        if not channels:
            msg = "a delivery router needs at least one channel"
            raise ValueError(msg)
        self._channels = tuple(channels)
        self._by_kind = {c.kind: c for c in channels}

    @property
    def channels(self) -> tuple[DeliveryChannel, ...]:
        return self._channels

    def deliver(self, subscriber: Subscriber, payload: AlertPayload) -> DeliveryReport:
        """Attempt delivery on every active endpoint the subscriber has."""
        attempts: list[DeliveryAttempt] = []

        for endpoint in subscriber.active_endpoints():
            channel = self._by_kind.get(endpoint.kind)
            if channel is None:
                attempts.append(
                    DeliveryAttempt(
                        channel=endpoint.kind,
                        outcome=DeliveryOutcome.UNCONFIGURED,
                        endpoint=endpoint.address,
                        detail="no channel registered for this endpoint kind",
                    )
                )
                continue

            if endpoint.kind is DeliveryChannelKind.SMS and not _sms_allowed(payload.severity):
                attempts.append(
                    DeliveryAttempt(
                        channel=endpoint.kind,
                        outcome=DeliveryOutcome.SKIPPED_POLICY,
                        endpoint=endpoint.address,
                        detail=f"SMS reserved for {SMS_MIN_SEVERITY.value} and above",
                    )
                )
                continue

            started = time.perf_counter()
            try:
                channel.send(endpoint, payload)
            except ChannelUnavailableError as exc:
                attempts.append(
                    DeliveryAttempt(
                        channel=endpoint.kind,
                        outcome=exc.outcome,
                        endpoint=endpoint.address,
                        detail=exc.detail,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                )
                continue
            except Exception as exc:  # noqa: BLE001 - one bad channel must not stop the rest
                attempts.append(
                    DeliveryAttempt(
                        channel=endpoint.kind,
                        outcome=DeliveryOutcome.ERROR,
                        endpoint=endpoint.address,
                        detail=f"{type(exc).__name__}: {exc}",
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                )
                continue

            attempts.append(
                DeliveryAttempt(
                    channel=endpoint.kind,
                    outcome=DeliveryOutcome.DELIVERED,
                    endpoint=endpoint.address,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            )

        return DeliveryReport(
            user_id=subscriber.user_id,
            alert_identifier=payload.alert_identifier,
            attempts=tuple(attempts),
        )


def _sms_allowed(severity: CapSeverity) -> bool:
    return SEVERITY_ORDER[severity] <= SEVERITY_ORDER[SMS_MIN_SEVERITY]


def payload_from_alert(
    alert: CapAlert,
    *,
    body: str,
    spoken_text: str,
    audio_cache_key: str | None = None,
    audio_bytes: bytes | None = None,
    handoff: HandoffNotice | None = None,
) -> AlertPayload:
    """Build the transport payload from a CAP alert and its rendered message."""
    info = alert.info[0]
    return AlertPayload(
        alert_identifier=alert.identifier,
        cap_xml=alert.to_xml(),
        headline=info.headline,
        body=body,
        spoken_text=spoken_text,
        language=info.language.value,
        severity=info.severity,
        audio_cache_key=audio_cache_key,
        audio_bytes=audio_bytes,
        handoff=handoff,
        sent_at=alert.sent,
    )
