"""Who gets alerted, and what ORCA knows about them (PLAN.md Phase 8.1, 8.3).

The alert subsystem is the one part of ORCA that acts without being asked, so the record of
*who* it may interrupt deserves more care than a query-time user object.

Three properties shape the design:

* **A subscriber is only evaluated while at sea.** A boat tied up in harbour does not need
  a wave warning, and waking someone at 3 a.m. for conditions they are not in is how a
  safety system gets muted — after which it warns nobody about anything.
* **Position is dated.** A vessel's last known position has an age, and past a threshold it
  stops being a basis for a geofence verdict. ORCA would rather say "I have not heard from
  this boat for two hours" than compute a confident drift projection from a stale fix.
* **Channels are ordered and each is honest about its reach.** In-app WebSocket only works
  with the app open; Web Push and FCM need a network the boat may not have. None of them
  reach a vessel beyond cellular coverage, which is why :mod:`orca_alerts.handoff` exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from orca_kernels import VesselProfile
from orca_speech import Language, VoiceGender

# A position older than this cannot support a geofence or drift verdict. Chosen against the
# physics rather than a round number: a vessel drifting at 1.5 kn covers roughly 1.4 km in
# half an hour, which is most of the 2 km red band — so a fix older than this could put a
# boat over the boundary while ORCA still reports it clear.
MAX_POSITION_AGE = timedelta(minutes=30)


class DeliveryChannelKind(StrEnum):
    """How an alert can reach a subscriber.

    Ordered by immediacy when the app is open, but see ``handoff``: none of these reach a
    vessel outside cellular range, and saying so plainly is part of the design.
    """

    IN_APP = "in_app"
    WEBSOCKET = "websocket"
    WEB_PUSH = "web_push"
    FCM = "fcm"
    SMS = "sms"


@dataclass(frozen=True)
class ChannelEndpoint:
    """One addressable destination for one channel."""

    kind: DeliveryChannelKind
    address: str
    # False once a provider reports the token dead, so a retired handset stops being
    # counted as a successful delivery.
    active: bool = True


@dataclass(frozen=True)
class VesselState:
    """Where a vessel was, when, and how it was moving.

    ``heading_deg`` and ``speed_knots`` come from the handset's GPS track. A stationary
    boat with nets out reports speed zero, which is a supported and important case: that is
    precisely the situation in which Palk Strait drift crossings happen.
    """

    lat: float
    lon: float
    reported_at: datetime
    heading_deg: float = 0.0
    speed_knots: float = 0.0
    at_sea: bool = True

    def age(self, now: datetime) -> timedelta:
        return now - self.reported_at

    def is_fresh(self, now: datetime, *, limit: timedelta = MAX_POSITION_AGE) -> bool:
        """Whether this fix is recent enough to reason about position from."""
        return self.age(now) <= limit


@dataclass(frozen=True)
class Subscriber:
    """One fisherman, one boat, one set of ways to reach them."""

    user_id: str
    vessel: VesselProfile
    language: Language
    state: VesselState | None = None
    endpoints: tuple[ChannelEndpoint, ...] = ()
    voice_gender: VoiceGender = VoiceGender.FEMALE
    # The harbour to name in a recall. Kept separate from the vessel's home port because a
    # boat 40 km down the coast should be sent to the nearest shelter, not home.
    home_harbour: str = ""
    # Authorities may broadcast to a fleet; a subscriber's membership decides who is in it.
    fleet_ids: frozenset[str] = frozenset()

    @property
    def is_at_sea(self) -> bool:
        return self.state is not None and self.state.at_sea

    def active_endpoints(self) -> tuple[ChannelEndpoint, ...]:
        return tuple(e for e in self.endpoints if e.active)

    def can_be_reached(self) -> bool:
        return bool(self.active_endpoints())

    def evaluable(self, now: datetime) -> bool:
        """Whether a position-based rule may be run for this subscriber.

        Returning False is not a failure. It is the correct answer for a boat in harbour or
        one whose last fix is too old to reason from, and the scheduler records the reason
        rather than skipping silently.
        """
        return self.is_at_sea and self.state is not None and self.state.is_fresh(now)


class SkipReason(StrEnum):
    """Why a subscriber was not evaluated on a tick.

    Recorded per tick because "no alerts were sent" has two completely different meanings —
    conditions were safe, or nobody could be assessed — and an operations view that cannot
    tell them apart is worse than no view.
    """

    ASHORE = "ashore"
    NO_POSITION = "no_position"
    STALE_POSITION = "stale_position"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class SkippedSubscriber:
    """A subscriber the tick could not assess, and why."""

    user_id: str
    reason: SkipReason
    detail: str = ""


def classify_skip(subscriber: Subscriber, now: datetime) -> SkippedSubscriber | None:
    """Why this subscriber cannot be evaluated, or ``None`` if they can be."""
    if subscriber.state is None:
        return SkippedSubscriber(subscriber.user_id, SkipReason.NO_POSITION)
    if not subscriber.state.at_sea:
        return SkippedSubscriber(subscriber.user_id, SkipReason.ASHORE)
    if not subscriber.state.is_fresh(now):
        age_minutes = subscriber.state.age(now).total_seconds() / 60
        return SkippedSubscriber(
            subscriber.user_id,
            SkipReason.STALE_POSITION,
            f"last fix is {age_minutes:.0f} min old",
        )
    if not subscriber.can_be_reached():
        return SkippedSubscriber(subscriber.user_id, SkipReason.UNREACHABLE)
    return None


@dataclass(frozen=True)
class Fleet:
    """A named group of vessels an authority can address.

    Exists so a broadcast names a real, listable set rather than a free-text region that
    nobody can later enumerate when asked who was warned.
    """

    fleet_id: str
    name: str
    authority: str
    member_user_ids: frozenset[str] = field(default_factory=frozenset)

    def includes(self, subscriber: Subscriber) -> bool:
        return (
            subscriber.user_id in self.member_user_ids
            or self.fleet_id in subscriber.fleet_ids
        )
