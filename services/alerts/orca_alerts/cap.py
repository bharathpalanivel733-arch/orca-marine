"""CAP 1.2 alert construction and XML emission (PLAN.md Phase 8.2).

The Common Alerting Protocol is what makes an ORCA alert interoperable with NDMA/SACHET
rather than a private notification format. OASIS CAP v1.2 is the standard; the element
names, ordering and enumerations below follow it, and the namespace is
``urn:oasis:names:tc:emergency:cap:1.2``.

**Severity, urgency and certainty are looked up from tables, not judged.** CAP's three axes
are exactly the kind of thing a language model would be happy to assign and unable to
justify, and they drive real downstream behaviour — a SACHET consumer routes on them. So
each is a pure function of the trigger that fired:

* **Severity** — how bad the consequence is, from the rule outcome.
* **Urgency** — how soon action is required, from the rule's own time horizon. Drift with
  eight minutes left is ``Immediate``; a cyclone cone containment is ``Future``.
* **Certainty** — how sure the evidence is. A measured containment in a published polygon
  is ``Observed``; a forecast threshold crossing is ``Likely``; a projection from a motion
  model is ``Likely`` and never ``Observed``, because a projection is not an observation.

Two structural choices worth stating:

* **ORCA is the sender, but not always the author.** A cyclone alert relays IMD's warning,
  and the CAP ``<source>`` says so. Presenting a relayed government warning as ORCA's own
  finding would be both a trust failure and, for a statutory warning, a legal one.
* **One ``<info>`` block per language.** CAP models multilingual alerts natively, so a Tamil
  fisherman and a Hindi-speaking harbour officer receive one alert with two info blocks,
  not two alerts that might diverge.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from xml.etree import ElementTree as ET

from orca_speech import Language

from orca_alerts.conditions import GeoPolygon
from orca_alerts.triggers import TriggerId, TriggerOutcome, TriggerResult

CAP_NAMESPACE = "urn:oasis:names:tc:emergency:cap:1.2"
CAP_VERSION = "1.2"

# The sender identifier ORCA signs alerts with. A domain-ish string is the CAP convention
# and makes the origin unambiguous to a downstream aggregator.
DEFAULT_SENDER = "orca@ocean-iq.in"


class CapStatus(StrEnum):
    ACTUAL = "Actual"
    EXERCISE = "Exercise"
    SYSTEM = "System"
    TEST = "Test"
    DRAFT = "Draft"


class CapMsgType(StrEnum):
    ALERT = "Alert"
    UPDATE = "Update"
    CANCEL = "Cancel"
    ACK = "Ack"
    ERROR = "Error"


class CapScope(StrEnum):
    PUBLIC = "Public"
    RESTRICTED = "Restricted"
    PRIVATE = "Private"


class CapCategory(StrEnum):
    MET = "Met"
    SAFETY = "Safety"
    SECURITY = "Security"
    RESCUE = "Rescue"
    ENV = "Env"
    TRANSPORT = "Transport"
    OTHER = "Other"


class CapSeverity(StrEnum):
    EXTREME = "Extreme"
    SEVERE = "Severe"
    MODERATE = "Moderate"
    MINOR = "Minor"
    UNKNOWN = "Unknown"


class CapUrgency(StrEnum):
    IMMEDIATE = "Immediate"
    EXPECTED = "Expected"
    FUTURE = "Future"
    PAST = "Past"
    UNKNOWN = "Unknown"


class CapCertainty(StrEnum):
    OBSERVED = "Observed"
    LIKELY = "Likely"
    POSSIBLE = "Possible"
    UNLIKELY = "Unlikely"
    UNKNOWN = "Unknown"


class CapResponseType(StrEnum):
    SHELTER = "Shelter"
    EVACUATE = "Evacuate"
    PREPARE = "Prepare"
    EXECUTE = "Execute"
    AVOID = "Avoid"
    MONITOR = "Monitor"
    ASSESS = "Assess"
    ALL_CLEAR = "AllClear"
    NONE = "None"


# --------------------------------------------------------------------------------------
# The mapping tables. This is where CAP's three axes are decided, once, in the open.
# --------------------------------------------------------------------------------------

# Rank for comparing severities. Declared explicitly rather than derived from enum order,
# so escalation logic does not silently change if a member is ever reordered.
SEVERITY_ORDER: dict[CapSeverity, int] = {
    CapSeverity.EXTREME: 0,
    CapSeverity.SEVERE: 1,
    CapSeverity.MODERATE: 2,
    CapSeverity.MINOR: 3,
    CapSeverity.UNKNOWN: 4,
}

SEVERITY_BY_OUTCOME: dict[TriggerOutcome, CapSeverity] = {
    TriggerOutcome.EMERGENCY: CapSeverity.EXTREME,
    TriggerOutcome.WARNING: CapSeverity.SEVERE,
    TriggerOutcome.WATCH: CapSeverity.MODERATE,
    TriggerOutcome.CLEAR: CapSeverity.MINOR,
    TriggerOutcome.UNEVALUATED: CapSeverity.UNKNOWN,
}

# Urgency is about *time to act*, which is a property of the hazard and not of its
# severity. A cyclone cone containment is severe in prospect but hours away; a drift
# crossing may be minor in consequence and minutes away.
URGENCY_BY_TRIGGER: dict[TriggerId, CapUrgency] = {
    TriggerId.CYCLONE_WIND: CapUrgency.IMMEDIATE,
    TriggerId.CYCLONE_CONE: CapUrgency.FUTURE,
    TriggerId.WAVE_HEIGHT: CapUrgency.EXPECTED,
    TriggerId.WIND_SPEED: CapUrgency.EXPECTED,
    TriggerId.LIGHTNING: CapUrgency.IMMEDIATE,
    TriggerId.SQUALL: CapUrgency.IMMEDIATE,
    TriggerId.GEOFENCE_PROXIMITY: CapUrgency.EXPECTED,
    TriggerId.GEOFENCE_DRIFT: CapUrgency.EXPECTED,
}

# Certainty reflects what kind of evidence produced the trigger.
#
# Containment in a published polygon and a measured distance to a surveyed boundary are
# observations. A threshold crossed in a *forecast* is Likely — the forecast may be wrong.
# A drift projection is Likely at best: it assumes a constant current and an unchanged
# helm, and calling a model's extrapolation "Observed" would overstate it to every
# downstream consumer.
CERTAINTY_BY_TRIGGER: dict[TriggerId, CapCertainty] = {
    TriggerId.CYCLONE_WIND: CapCertainty.OBSERVED,
    TriggerId.CYCLONE_CONE: CapCertainty.POSSIBLE,
    TriggerId.WAVE_HEIGHT: CapCertainty.LIKELY,
    TriggerId.WIND_SPEED: CapCertainty.LIKELY,
    TriggerId.LIGHTNING: CapCertainty.OBSERVED,
    TriggerId.SQUALL: CapCertainty.POSSIBLE,
    TriggerId.GEOFENCE_PROXIMITY: CapCertainty.OBSERVED,
    TriggerId.GEOFENCE_DRIFT: CapCertainty.LIKELY,
}

CATEGORY_BY_TRIGGER: dict[TriggerId, CapCategory] = {
    TriggerId.CYCLONE_WIND: CapCategory.MET,
    TriggerId.CYCLONE_CONE: CapCategory.MET,
    TriggerId.WAVE_HEIGHT: CapCategory.MET,
    TriggerId.WIND_SPEED: CapCategory.MET,
    TriggerId.LIGHTNING: CapCategory.MET,
    TriggerId.SQUALL: CapCategory.MET,
    # Not Met: an IMBL crossing is a legal and security event, not a weather one, and a
    # SACHET consumer routing on category should not file it with the storms.
    TriggerId.GEOFENCE_PROXIMITY: CapCategory.SECURITY,
    TriggerId.GEOFENCE_DRIFT: CapCategory.SECURITY,
}

RESPONSE_BY_TRIGGER: dict[TriggerId, CapResponseType] = {
    TriggerId.CYCLONE_WIND: CapResponseType.EVACUATE,
    TriggerId.CYCLONE_CONE: CapResponseType.MONITOR,
    TriggerId.WAVE_HEIGHT: CapResponseType.AVOID,
    TriggerId.WIND_SPEED: CapResponseType.AVOID,
    TriggerId.LIGHTNING: CapResponseType.SHELTER,
    TriggerId.SQUALL: CapResponseType.SHELTER,
    TriggerId.GEOFENCE_PROXIMITY: CapResponseType.AVOID,
    TriggerId.GEOFENCE_DRIFT: CapResponseType.EXECUTE,
}


def severity_for(result: TriggerResult) -> CapSeverity:
    """CAP severity, from the rule outcome alone."""
    return SEVERITY_BY_OUTCOME[result.outcome]


def urgency_for(result: TriggerResult) -> CapUrgency:
    """CAP urgency, from the trigger and — for drift — the time actually remaining.

    Drift is the one rule whose urgency is not fixed by its kind: the same rule fires for a
    crossing in forty minutes and one in six, and those are not the same instruction.
    """
    if result.trigger_id is TriggerId.GEOFENCE_DRIFT:
        minutes = result.observed.get("time_to_boundary_minutes")
        if isinstance(minutes, int | float):
            return CapUrgency.IMMEDIATE if minutes <= 10 else CapUrgency.EXPECTED
    if result.outcome is TriggerOutcome.EMERGENCY:
        return CapUrgency.IMMEDIATE
    return URGENCY_BY_TRIGGER[result.trigger_id]


def certainty_for(result: TriggerResult) -> CapCertainty:
    """CAP certainty, from the kind of evidence behind the trigger."""
    return CERTAINTY_BY_TRIGGER[result.trigger_id]


@dataclass(frozen=True)
class CapArea:
    """The ``<area>`` block: where the alert applies."""

    description: str
    polygon: GeoPolygon | None = None
    # CAP ``<circle>`` is "lat,lon radius_km" — the right shape for "around this vessel".
    circle_lat: float | None = None
    circle_lon: float | None = None
    circle_radius_km: float | None = None

    @property
    def circle(self) -> str | None:
        if self.circle_lat is None or self.circle_lon is None or self.circle_radius_km is None:
            return None
        return f"{self.circle_lat:.4f},{self.circle_lon:.4f} {self.circle_radius_km:.1f}"


@dataclass(frozen=True)
class CapInfo:
    """One ``<info>`` block — the alert in one language."""

    language: Language
    category: CapCategory
    event: str
    urgency: CapUrgency
    severity: CapSeverity
    certainty: CapCertainty
    headline: str
    description: str
    instruction: str
    sender_name: str = "ORCA — Team OCEAN-IQ"
    response_type: CapResponseType = CapResponseType.NONE
    effective: datetime | None = None
    expires: datetime | None = None
    # The authority whose warning this relays; ORCA does not author cyclone warnings.
    source: str | None = None
    web: str | None = None
    # CAP ``<parameter>`` carries structured detail — here, the arithmetic that fired.
    parameters: tuple[tuple[str, str], ...] = ()
    # ``<resource>`` for the synthesized audio (Phase 7.9, 8.3).
    audio_uri: str | None = None
    audio_mime_type: str = "audio/wav"
    audio_size_bytes: int | None = None
    areas: tuple[CapArea, ...] = ()

    @property
    def bcp47(self) -> str:
        """CAP ``<language>`` wants a BCP-47 tag; ORCA's are Indian-locale variants."""
        return f"{self.language.value}-IN"


@dataclass(frozen=True)
class CapAlert:
    """A complete CAP 1.2 alert."""

    identifier: str
    sender: str
    sent: datetime
    status: CapStatus
    msg_type: CapMsgType
    scope: CapScope
    info: tuple[CapInfo, ...]
    # Required by CAP for Update and Cancel: which alert this supersedes.
    references: tuple[str, ...] = ()
    addresses: tuple[str, ...] = ()
    restriction: str | None = None
    note: str | None = None
    incidents: str | None = None

    def __post_init__(self) -> None:
        if not self.info:
            msg = "a CAP alert needs at least one <info> block"
            raise ValueError(msg)
        if self.msg_type in {CapMsgType.UPDATE, CapMsgType.CANCEL} and not self.references:
            # CAP requires it, and without it a consumer cannot tell what was superseded —
            # which turns an update into a second, contradictory warning.
            msg = f"{self.msg_type.value} requires <references> to the alert it supersedes"
            raise ValueError(msg)
        if self.scope is CapScope.RESTRICTED and not self.restriction:
            msg = "Restricted scope requires a <restriction>"
            raise ValueError(msg)
        if self.scope is CapScope.PRIVATE and not self.addresses:
            msg = "Private scope requires <addresses>"
            raise ValueError(msg)

    @property
    def max_severity(self) -> CapSeverity:
        """The most severe of the info blocks, for routing and escalation comparisons.

        ``CapSeverity`` is declared most-severe-first, so the smallest index wins.
        """
        return min(self.info, key=lambda i: SEVERITY_ORDER[i.severity]).severity

    def to_xml(self) -> str:
        """Serialize to CAP 1.2 XML."""
        return ET.tostring(self.to_element(), encoding="unicode", xml_declaration=True)

    def to_element(self) -> ET.Element:
        """Build the ``<alert>`` element.

        Child order follows the CAP 1.2 schema sequence. CAP validators enforce it, so the
        order below is a requirement rather than a style choice.
        """
        alert = ET.Element("alert", {"xmlns": CAP_NAMESPACE})
        _text(alert, "identifier", self.identifier)
        _text(alert, "sender", self.sender)
        _text(alert, "sent", _cap_time(self.sent))
        _text(alert, "status", self.status.value)
        _text(alert, "msgType", self.msg_type.value)
        _text(alert, "scope", self.scope.value)
        if self.restriction:
            _text(alert, "restriction", self.restriction)
        if self.addresses:
            # CAP quotes addresses containing whitespace; ORCA's are ids, but quoting is
            # the safe form and every consumer accepts it.
            _text(alert, "addresses", " ".join(f'"{a}"' for a in self.addresses))
        if self.note:
            _text(alert, "note", self.note)
        if self.references:
            _text(alert, "references", " ".join(self.references))
        if self.incidents:
            _text(alert, "incidents", self.incidents)

        for info in self.info:
            alert.append(_info_element(info))
        return alert


def _info_element(info: CapInfo) -> ET.Element:
    element = ET.Element("info")
    _text(element, "language", info.bcp47)
    _text(element, "category", info.category.value)
    _text(element, "event", info.event)
    if info.response_type is not CapResponseType.NONE:
        _text(element, "responseType", info.response_type.value)
    _text(element, "urgency", info.urgency.value)
    _text(element, "severity", info.severity.value)
    _text(element, "certainty", info.certainty.value)
    if info.effective:
        _text(element, "effective", _cap_time(info.effective))
    if info.expires:
        _text(element, "expires", _cap_time(info.expires))
    _text(element, "senderName", info.sender_name)
    _text(element, "headline", info.headline)
    _text(element, "description", info.description)
    _text(element, "instruction", info.instruction)
    if info.web:
        _text(element, "web", info.web)
    if info.source:
        # Not part of CAP's <info> sequence as a first-class element, so the relayed
        # authority is carried as a parameter where consumers can find it reliably.
        _parameter(element, "origin_authority", info.source)
    for name, value in info.parameters:
        _parameter(element, name, value)
    if info.audio_uri:
        resource = ET.SubElement(element, "resource")
        _text(resource, "resourceDesc", "Spoken alert")
        _text(resource, "mimeType", info.audio_mime_type)
        if info.audio_size_bytes is not None:
            _text(resource, "size", str(info.audio_size_bytes))
        _text(resource, "uri", info.audio_uri)
    for area in info.areas:
        element.append(_area_element(area))
    return element


def _area_element(area: CapArea) -> ET.Element:
    element = ET.Element("area")
    _text(element, "areaDesc", area.description)
    if area.polygon is not None:
        _text(element, "polygon", area.polygon.to_cap_polygon())
    circle = area.circle
    if circle is not None:
        _text(element, "circle", circle)
    return element


def _text(parent: ET.Element, tag: str, value: str) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = value
    return child


def _parameter(parent: ET.Element, name: str, value: str) -> None:
    parameter = ET.SubElement(parent, "parameter")
    _text(parameter, "valueName", name)
    _text(parameter, "value", value)


def _cap_time(moment: datetime) -> str:
    """CAP requires a timezone offset and forbids 'Z'.

    Python renders UTC as ``+00:00``, which is what CAP wants — but a naive datetime would
    silently produce an offsetless string that a validator rejects, so it is refused here.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        msg = "CAP timestamps must be timezone-aware"
        raise ValueError(msg)
    return moment.isoformat(timespec="seconds")


def make_identifier(*, user_id: str, trigger_id: TriggerId, sent: datetime, salt: str = "") -> str:
    """A stable, unique CAP identifier.

    Derived rather than random so the same alert regenerated from the same inputs carries
    the same identifier — which is what lets the audit trail and a downstream consumer agree
    on what was sent. The timestamp is included because a later alert for the same rule is a
    genuinely different alert.
    """
    material = f"{user_id}|{trigger_id.value}|{sent.isoformat()}|{salt}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"orca.{trigger_id.value}.{digest}"


@dataclass(frozen=True)
class CapValidationIssue:
    """One structural problem found in an alert."""

    element: str
    message: str


def validate(alert: CapAlert) -> tuple[CapValidationIssue, ...]:
    """Structural checks beyond what the constructor enforces.

    Deliberately a report rather than an exception: this runs in tests and in an operations
    view, where listing every problem at once beats failing on the first.
    """
    issues: list[CapValidationIssue] = []
    if not alert.identifier or " " in alert.identifier:
        issues.append(CapValidationIssue("identifier", "must be non-empty and contain no spaces"))
    if not alert.sender or " " in alert.sender:
        issues.append(CapValidationIssue("sender", "must be non-empty and contain no spaces"))

    seen_languages: set[str] = set()
    for info in alert.info:
        if info.bcp47 in seen_languages:
            issues.append(
                CapValidationIssue("info/language", f"duplicate info block for {info.bcp47}")
            )
        seen_languages.add(info.bcp47)
        if not info.headline:
            issues.append(CapValidationIssue("info/headline", "must not be empty"))
        if len(info.headline) > 160:
            # CAP recommends 160 characters; longer headlines get truncated by consumers,
            # which is how an instruction loses its verb.
            issues.append(
                CapValidationIssue("info/headline", "should be 160 characters or fewer")
            )
        if not info.instruction:
            issues.append(
                CapValidationIssue(
                    "info/instruction", "must tell the recipient what to do"
                )
            )
        if info.expires and info.effective and info.expires <= info.effective:
            issues.append(CapValidationIssue("info/expires", "must be after effective"))
        if not info.areas:
            issues.append(CapValidationIssue("info/area", "an alert with no area cannot be routed"))
    return tuple(issues)
