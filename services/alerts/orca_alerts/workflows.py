"""Disaster-management workflows (PLAN.md Phase 8.4, gap G9).

Four things a coastal authority actually does when weather turns, expressed as data rather
than prose:

* **Boat recall** — order vessels in an area back to harbour, and know which ones were told.
* **Harbour advisory** — a port-level instruction: no sailing, restricted sailing, or clear.
* **Cyclone-shelter guidance** — the nearest designated shelter to a landing point.
* **Authority broadcast** — one message to a named region or fleet, on the authority's own
  identity, not ORCA's.

The distinction that governs all four: **ORCA relays authority decisions, it does not make
them.** A boat recall is an administrative act with legal weight; ORCA's role is to carry it
accurately, record who it reached, and say who issued it. So every workflow here carries an
``issued_by`` and produces an auditable list of recipients — the question after an incident
is always "who was told, and when", and a system that cannot answer it has not helped.

The shelter registry is small, hand-entered and explicitly marked as unverified. A shelter
list that turns out to be wrong sends people to a locked building in a cyclone, so it is
better to ship three entries labelled as samples than three hundred scraped from an
unverifiable source.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from orca_speech import Language

from orca_alerts.cap import (
    CapAlert,
    CapArea,
    CapCategory,
    CapCertainty,
    CapInfo,
    CapMsgType,
    CapResponseType,
    CapScope,
    CapSeverity,
    CapStatus,
    CapUrgency,
    make_identifier,
)
from orca_alerts.conditions import GeoPolygon
from orca_alerts.subscriber import Fleet, Subscriber
from orca_alerts.triggers import TriggerId

# Shelters are a **sample registry, not an authoritative list.** Real deployment must load
# the state disaster-management authority's own register; sending someone to a shelter that
# is closed or does not exist is a worse outcome than sending them to a named harbour.
SHELTER_REGISTRY_IS_SAMPLE = True


class HarbourStatus(StrEnum):
    """What a harbour is telling its fleet."""

    OPEN = "open"
    RESTRICTED = "restricted"
    CLOSED = "closed"


class RecallUrgency(StrEnum):
    """How fast a recall expects boats back."""

    ADVISORY = "advisory"
    IMMEDIATE = "immediate"


@dataclass(frozen=True)
class CycloneShelter:
    """A designated shelter, as a state authority registers it."""

    shelter_id: str
    name: str
    lat: float
    lon: float
    district: str
    capacity: int | None = None
    verified: bool = False


# Three sample entries covering the demo coastline. Marked unverified, and the flag is
# surfaced in the guidance rather than hidden, so nobody mistakes this for a register.
SAMPLE_SHELTERS: tuple[CycloneShelter, ...] = (
    CycloneShelter(
        "tn-ngp-01", "Nagapattinam Multipurpose Cyclone Shelter", 10.76, 79.84, "Nagapattinam"
    ),
    CycloneShelter("tn-rmd-01", "Rameswaram Relief Centre", 9.29, 79.31, "Ramanathapuram"),
    CycloneShelter("tn-cdl-01", "Cuddalore Coastal Shelter", 11.71, 79.77, "Cuddalore"),
)


@dataclass(frozen=True)
class ShelterGuidance:
    """Where to go, and how far it is."""

    shelter: CycloneShelter
    distance_km: float
    caveat: str = ""

    @property
    def is_verified(self) -> bool:
        return self.shelter.verified


def nearest_shelter(
    lat: float, lon: float, *, shelters: Sequence[CycloneShelter] = SAMPLE_SHELTERS
) -> ShelterGuidance | None:
    """The closest registered shelter to a position.

    Straight-line distance on an equirectangular approximation, which is accurate enough at
    these scales for choosing between shelters tens of kilometres apart. It is **not** a
    routing distance and does not claim to be — the road may be flooded, which is precisely
    the situation a cyclone creates.
    """
    if not shelters:
        return None

    import math

    def separation_km(shelter: CycloneShelter) -> float:
        mean_lat = math.radians((lat + shelter.lat) / 2)
        dx = (shelter.lon - lon) * math.cos(mean_lat) * 111.32
        dy = (shelter.lat - lat) * 110.57
        return math.hypot(dx, dy)

    closest = min(shelters, key=separation_km)
    caveat = (
        "Shelter list is a sample and has not been verified against the state register. "
        "Confirm locally before relying on it."
        if SHELTER_REGISTRY_IS_SAMPLE and not closest.verified
        else ""
    )
    return ShelterGuidance(shelter=closest, distance_km=separation_km(closest), caveat=caveat)


@dataclass(frozen=True)
class BoatRecall:
    """An order for vessels in an area to return to harbour."""

    recall_id: str
    issued_by: str
    issued_at: datetime
    area_description: str
    urgency: RecallUrgency
    reason: str
    area: GeoPolygon | None = None
    expires_at: datetime | None = None

    @property
    def cap_severity(self) -> CapSeverity:
        return (
            CapSeverity.EXTREME
            if self.urgency is RecallUrgency.IMMEDIATE
            else CapSeverity.SEVERE
        )

    @property
    def cap_urgency(self) -> CapUrgency:
        return (
            CapUrgency.IMMEDIATE
            if self.urgency is RecallUrgency.IMMEDIATE
            else CapUrgency.EXPECTED
        )


@dataclass(frozen=True)
class HarbourAdvisory:
    """A port-level sailing instruction."""

    harbour_id: str
    harbour_name: str
    status: HarbourStatus
    issued_by: str
    issued_at: datetime
    reason: str
    expires_at: datetime | None = None

    @property
    def cap_severity(self) -> CapSeverity:
        return {
            HarbourStatus.CLOSED: CapSeverity.SEVERE,
            HarbourStatus.RESTRICTED: CapSeverity.MODERATE,
            HarbourStatus.OPEN: CapSeverity.MINOR,
        }[self.status]

    @property
    def response_type(self) -> CapResponseType:
        return (
            CapResponseType.ALL_CLEAR
            if self.status is HarbourStatus.OPEN
            else CapResponseType.AVOID
        )


@dataclass(frozen=True)
class AuthorityBroadcast:
    """One message from an authority to a region or a named fleet."""

    broadcast_id: str
    issued_by: str
    issued_at: datetime
    headline: str
    body: str
    instruction: str
    severity: CapSeverity
    urgency: CapUrgency
    certainty: CapCertainty
    languages: tuple[Language, ...] = (Language.ENGLISH,)
    area_description: str = ""
    area: GeoPolygon | None = None
    fleet: Fleet | None = None
    expires_at: datetime | None = None

    def recipients(self, subscribers: Sequence[Subscriber]) -> tuple[Subscriber, ...]:
        """Who this broadcast addresses.

        A fleet broadcast addresses its members; an area broadcast addresses everyone whose
        last known position falls inside the polygon. Subscribers with no position are
        **included** in an area broadcast rather than excluded: not knowing where a boat is
        is not a reason to leave it out of a cyclone warning.
        """
        if self.fleet is not None:
            return tuple(s for s in subscribers if self.fleet.includes(s))
        if self.area is None:
            return tuple(subscribers)
        return tuple(
            s
            for s in subscribers
            if s.state is None or self.area.contains(s.state.lat, s.state.lon)
        )


def recall_to_cap(
    recall: BoatRecall,
    *,
    languages: Sequence[Language] = (Language.ENGLISH,),
    sender: str = "orca@ocean-iq.in",
) -> CapAlert:
    """Render a boat recall as CAP.

    Scope is ``Public``: a recall is a public safety instruction, and restricting it would
    stop it propagating to the aggregators that need to carry it.
    """
    area = CapArea(description=recall.area_description, polygon=recall.area)
    infos = tuple(
        CapInfo(
            language=language,
            category=CapCategory.SAFETY,
            event="Boat recall",
            urgency=recall.cap_urgency,
            severity=recall.cap_severity,
            # An authority has decided this; it is not a forecast.
            certainty=CapCertainty.OBSERVED,
            headline=f"Return to harbour: {recall.area_description}",
            description=recall.reason,
            instruction="Return to the nearest harbour now. Do not continue fishing.",
            response_type=CapResponseType.EVACUATE,
            sender_name=recall.issued_by,
            source=recall.issued_by,
            effective=recall.issued_at,
            expires=recall.expires_at,
            parameters=(
                ("workflow", "boat_recall"),
                ("recall_id", recall.recall_id),
                ("recall_urgency", recall.urgency.value),
            ),
            areas=(area,),
        )
        for language in languages
    )
    return CapAlert(
        identifier=make_identifier(
            user_id=recall.recall_id,
            trigger_id=TriggerId.CYCLONE_WIND,
            sent=recall.issued_at,
            salt="boat_recall",
        ),
        sender=sender,
        sent=recall.issued_at,
        status=CapStatus.ACTUAL,
        msg_type=CapMsgType.ALERT,
        scope=CapScope.PUBLIC,
        info=infos,
    )


def harbour_advisory_to_cap(
    advisory: HarbourAdvisory,
    *,
    languages: Sequence[Language] = (Language.ENGLISH,),
    sender: str = "orca@ocean-iq.in",
) -> CapAlert:
    """Render a harbour advisory as CAP."""
    instruction = {
        HarbourStatus.CLOSED: "Do not put out to sea from this harbour.",
        HarbourStatus.RESTRICTED: (
            "Sailing is restricted. Check with the harbour office before putting out."
        ),
        HarbourStatus.OPEN: "The harbour is open. Normal sailing has resumed.",
    }[advisory.status]

    infos = tuple(
        CapInfo(
            language=language,
            category=CapCategory.TRANSPORT,
            event="Harbour advisory",
            urgency=CapUrgency.EXPECTED,
            severity=advisory.cap_severity,
            certainty=CapCertainty.OBSERVED,
            headline=f"{advisory.harbour_name}: harbour {advisory.status.value}",
            description=advisory.reason,
            instruction=instruction,
            response_type=advisory.response_type,
            sender_name=advisory.issued_by,
            source=advisory.issued_by,
            effective=advisory.issued_at,
            expires=advisory.expires_at,
            parameters=(
                ("workflow", "harbour_advisory"),
                ("harbour_id", advisory.harbour_id),
                ("harbour_status", advisory.status.value),
            ),
            areas=(CapArea(description=advisory.harbour_name),),
        )
        for language in languages
    )
    return CapAlert(
        identifier=make_identifier(
            user_id=advisory.harbour_id,
            trigger_id=TriggerId.WIND_SPEED,
            sent=advisory.issued_at,
            salt="harbour_advisory",
        ),
        sender=sender,
        sent=advisory.issued_at,
        status=CapStatus.ACTUAL,
        msg_type=CapMsgType.ALERT,
        scope=CapScope.PUBLIC,
        info=infos,
    )


def broadcast_to_cap(
    broadcast: AuthorityBroadcast, *, sender: str = "orca@ocean-iq.in"
) -> CapAlert:
    """Render an authority broadcast as CAP.

    ``senderName`` and ``origin_authority`` carry the authority's identity, not ORCA's.
    ORCA is the transport; attributing a government instruction to a hackathon project would
    be both misleading and, for a statutory warning, improper.
    """
    area = CapArea(
        description=(
            broadcast.area_description or (broadcast.fleet.name if broadcast.fleet else "All")
        ),
        polygon=broadcast.area,
    )
    infos = tuple(
        CapInfo(
            language=language,
            category=CapCategory.SAFETY,
            event="Authority broadcast",
            urgency=broadcast.urgency,
            severity=broadcast.severity,
            certainty=broadcast.certainty,
            headline=broadcast.headline,
            description=broadcast.body,
            instruction=broadcast.instruction,
            sender_name=broadcast.issued_by,
            source=broadcast.issued_by,
            effective=broadcast.issued_at,
            expires=broadcast.expires_at,
            parameters=(
                ("workflow", "authority_broadcast"),
                ("broadcast_id", broadcast.broadcast_id),
                *(
                    (("fleet_id", broadcast.fleet.fleet_id),)
                    if broadcast.fleet is not None
                    else ()
                ),
            ),
            areas=(area,),
        )
        for language in broadcast.languages
    )
    return CapAlert(
        identifier=make_identifier(
            user_id=broadcast.broadcast_id,
            trigger_id=TriggerId.CYCLONE_CONE,
            sent=broadcast.issued_at,
            salt="authority_broadcast",
        ),
        sender=sender,
        sent=broadcast.issued_at,
        status=CapStatus.ACTUAL,
        msg_type=CapMsgType.ALERT,
        scope=CapScope.PUBLIC,
        info=infos,
    )


@dataclass(frozen=True)
class WorkflowDispatch:
    """The result of running a workflow: the CAP alert and who it addressed."""

    alert: CapAlert
    recipients: tuple[str, ...] = field(default_factory=tuple)
    workflow: str = ""

    @property
    def recipient_count(self) -> int:
        return len(self.recipients)


def dispatch_recall(
    recall: BoatRecall,
    subscribers: Sequence[Subscriber],
    *,
    languages: Sequence[Language] = (Language.ENGLISH,),
) -> WorkflowDispatch:
    """Address a recall to the vessels it concerns.

    Only boats currently at sea: a recall telling someone in harbour to return to harbour is
    noise, and noise is what gets a safety channel muted. Vessels with no position **are**
    included when the recall has an area, for the same reason as a broadcast — an unknown
    position is not evidence of safety.
    """
    at_sea = [s for s in subscribers if s.is_at_sea]
    if recall.area is None:
        addressed = at_sea
    else:
        addressed = [
            s
            for s in at_sea
            if s.state is None or recall.area.contains(s.state.lat, s.state.lon)
        ]
    return WorkflowDispatch(
        alert=recall_to_cap(recall, languages=languages),
        recipients=tuple(s.user_id for s in addressed),
        workflow="boat_recall",
    )
