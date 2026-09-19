"""Offshore hand-off to GEMINI, DAT-SG and Sagarmitra (PLAN.md Phase 8.5).

**ORCA is not an offshore communications channel and must never be presented as one.**

Every delivery path in :mod:`orca_alerts.delivery` needs an IP connection. Indian coastal
cellular coverage runs to roughly 10–20 km offshore depending on terrain and operator, and
beyond that a phone has no data, so an ORCA alert composed for a vessel 60 km out is a
message that will sit in a queue until the boat comes home. Claiming otherwise would be the
single most dangerous overstatement this project could make: a fisherman who believes ORCA
will warn him offshore is a fisherman who stops watching the sky.

The services that *do* reach offshore already exist, are operational, and are funded by the
Government of India:

* **GEMINI** (Gagan Enabled Mariner's Instrument for Navigation and Information) — ISRO's
  GAGAN-based satellite receiver, pairing to a phone over Bluetooth. Broadcasts INCOIS/IMD
  warnings well beyond cellular range and is the designated offshore channel for fishermen.
* **DAT-SG** (Distress Alert Transmitter – Second Generation) — ISRO's satellite distress
  beacon, routed to the Indian Coast Guard MRCC.
* **Sagarmitra / Samudra** — the INCOIS advisory dissemination programme and its app, which
  covers the shore-side and near-shore leg through the fisherfolk network.

ORCA's job is to be **honest about where its own reach ends** and to route the user to the
right service at that point, in the UI and on stage. That is a stronger position than a
false claim, not a weaker one: it shows the system knows its own limits, which is exactly
what a jury evaluating a safety product should want to see.

This module produces that hand-off as data, so it can be rendered in the UI, attached to an
alert and asserted in a test rather than living only in a slide.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Where ORCA's own delivery stops being dependable.
#
# A range, not a line, because coverage varies with operator, terrain and sea state. The
# conservative end is what the UI warns at: telling someone they are covered when they are
# marginal is the error that matters.
RELIABLE_COVERAGE_KM = 10.0
MARGINAL_COVERAGE_KM = 20.0


class CoverageZone(StrEnum):
    """How far offshore a vessel is, relative to ORCA's own reach."""

    NEARSHORE = "nearshore"
    MARGINAL = "marginal"
    OFFSHORE = "offshore"


@dataclass(frozen=True)
class OffshoreService:
    """An operational service that reaches where ORCA does not."""

    service_id: str
    name: str
    operator: str
    description: str
    covers: str
    how_to_use: str


GEMINI = OffshoreService(
    service_id="gemini",
    name="GEMINI",
    operator="ISRO / INCOIS",
    description=(
        "GAGAN Enabled Mariner's Instrument for Navigation and Information. A satellite "
        "receiver that pairs to a phone over Bluetooth and receives INCOIS and IMD warnings "
        "beyond cellular range."
    ),
    covers="Emergency and weather warnings offshore, well past mobile coverage.",
    how_to_use=(
        "Carry a GEMINI device, paired and switched on, whenever going beyond sight of land."
    ),
)

DAT_SG = OffshoreService(
    service_id="dat_sg",
    name="DAT-SG",
    operator="ISRO / Indian Coast Guard",
    description=(
        "Distress Alert Transmitter, second generation. A satellite distress beacon whose "
        "alert is routed to the Coast Guard Maritime Rescue Coordination Centre."
    ),
    covers="Distress alerting from anywhere at sea, with no dependence on a phone network.",
    how_to_use="Press the DAT-SG distress button. It does not need ORCA, a phone or a signal.",
)

SAGARMITRA = OffshoreService(
    service_id="sagarmitra",
    name="Sagarmitra / Samudra",
    operator="INCOIS",
    description=(
        "INCOIS advisory dissemination through the fisherfolk network and the Samudra app, "
        "covering the shore-side and near-shore leg."
    ),
    covers="Potential fishing zone and ocean state advisories in harbour and near shore.",
    how_to_use="Follow local Sagarmitra advisories before setting out.",
)

OFFSHORE_SERVICES: tuple[OffshoreService, ...] = (GEMINI, DAT_SG, SAGARMITRA)


def coverage_zone(distance_from_shore_km: float) -> CoverageZone:
    """Classify how far offshore a position is."""
    if distance_from_shore_km <= RELIABLE_COVERAGE_KM:
        return CoverageZone.NEARSHORE
    if distance_from_shore_km <= MARGINAL_COVERAGE_KM:
        return CoverageZone.MARGINAL
    return CoverageZone.OFFSHORE


@dataclass(frozen=True)
class HandoffNotice:
    """What to tell a user about the limits of ORCA's reach at their position."""

    zone: CoverageZone
    distance_from_shore_km: float
    orca_can_deliver: bool
    message: str
    services: tuple[OffshoreService, ...]

    @property
    def should_display(self) -> bool:
        """Whether the UI must show this. Never hidden outside the nearshore zone."""
        return self.zone is not CoverageZone.NEARSHORE

    def as_dict(self) -> dict[str, object]:
        return {
            "zone": self.zone.value,
            "distance_from_shore_km": round(self.distance_from_shore_km, 1),
            "orca_can_deliver": self.orca_can_deliver,
            "message": self.message,
            "services": [
                {
                    "service_id": s.service_id,
                    "name": s.name,
                    "operator": s.operator,
                    "covers": s.covers,
                    "how_to_use": s.how_to_use,
                }
                for s in self.services
            ],
        }


def handoff_for(distance_from_shore_km: float) -> HandoffNotice:
    """The hand-off notice for a position.

    The wording is blunt on purpose. A hedged "coverage may be limited" reads as boilerplate
    and gets ignored; "ORCA cannot reach you here" is the thing that needs to land.
    """
    zone = coverage_zone(distance_from_shore_km)

    if zone is CoverageZone.NEARSHORE:
        return HandoffNotice(
            zone=zone,
            distance_from_shore_km=distance_from_shore_km,
            orca_can_deliver=True,
            message="You are within mobile coverage. ORCA alerts should reach this phone.",
            services=(),
        )

    if zone is CoverageZone.MARGINAL:
        return HandoffNotice(
            zone=zone,
            distance_from_shore_km=distance_from_shore_km,
            orca_can_deliver=False,
            message=(
                "You are near the edge of mobile coverage. ORCA alerts may not reach you. "
                "Use a GEMINI device for warnings and DAT-SG for distress."
            ),
            services=(GEMINI, DAT_SG),
        )

    return HandoffNotice(
        zone=zone,
        distance_from_shore_km=distance_from_shore_km,
        orca_can_deliver=False,
        message=(
            "ORCA cannot reach you at this distance from shore. It is not a satellite "
            "service. Rely on GEMINI for warnings and DAT-SG for distress."
        ),
        services=(GEMINI, DAT_SG),
    )


# The sentence ORCA says on stage and shows in the UI. Kept here as a constant so the
# claim is version-controlled and testable, rather than living only in a slide deck where
# it can quietly drift into an overstatement.
HANDOFF_STATEMENT = (
    "ORCA delivers over mobile data and does not provide an offshore channel. Beyond "
    "cellular coverage, ORCA defers to GEMINI (GAGAN/NavIC) for warnings, DAT-SG for "
    "distress alerting, and INCOIS Sagarmitra for advisory dissemination. ORCA complements "
    "these services and does not replace or compete with them."
)
