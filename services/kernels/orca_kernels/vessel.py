"""Vessel profiles and class thresholds (PLAN.md Phase 4.1).

The Tier-1 differentiator behind the safety kernel: **the same 2.5 m sea state is routine
for a mechanized trawler and lethal for an FRP vallam.** Incumbent systems publish one
colour-coded sea-state band for everyone; ORCA normalises every driver against the
thresholds of the boat actually asking.

Thresholds are stated per class with their source, and are deliberately conservative
where evidence is thin. METHODS.md §1 gives the anchor: FRP vallam / catamaran —
Hs < 1.5 m "caution", < 2 m "avoid"; mechanized trawler higher. The rest follow the same
shape, scaled by the seakeeping the class actually has.

These are **engineering defaults, not regulation**. They are the numbers a jury will
probe, so each carries a rationale and each can be overridden per vessel: a
well-found 12 m gillnetter with an experienced crew is not the fleet average, and the
profile can say so.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from orca_schemas import OrcaModel
from pydantic import Field, model_validator


class VesselClass(StrEnum):
    """Boat classes ORCA reasons about, from least to most seaworthy."""

    CATAMARAN = "catamaran"
    FRP_VALLAM = "frp_vallam"
    GILLNETTER = "gillnetter"
    MECHANIZED_TRAWLER = "mechanized_trawler"


class SafetyEquipment(StrEnum):
    """Equipment that changes what a boat can survive or summon.

    Modelled because it changes the *confidence* in a safety verdict rather than the sea
    state: a boat with no VHF and no EPIRB has no margin if the forecast is wrong.
    """

    LIFEJACKETS = "lifejackets"
    VHF_RADIO = "vhf_radio"
    GPS = "gps"
    EPIRB = "epirb"
    LIFE_RAFT = "life_raft"
    NAVIC_GEMINI = "navic_gemini"


class ClassThresholds(OrcaModel):
    """Where a class moves from routine, to caution, to avoid.

    Two thresholds per driver rather than one: a single cut-off would make the safety
    score a step function, and a fisherman a metre from the line deserves a graded
    answer, not a cliff.
    """

    caution_wave_height_m: float = Field(gt=0)
    avoid_wave_height_m: float = Field(gt=0)
    caution_wind_speed_ms: float = Field(gt=0)
    avoid_wind_speed_ms: float = Field(gt=0)
    # Short, steep swell is worse than long swell of the same height for a small hull,
    # so the period threshold is a LOWER bound: below it, conditions are penalised.
    min_comfortable_swell_period_s: float = Field(gt=0)
    rationale: str

    @model_validator(mode="after")
    def _avoid_exceeds_caution(self) -> Self:
        if self.avoid_wave_height_m <= self.caution_wave_height_m:
            msg = "avoid wave height must exceed caution wave height"
            raise ValueError(msg)
        if self.avoid_wind_speed_ms <= self.caution_wind_speed_ms:
            msg = "avoid wind speed must exceed caution wind speed"
            raise ValueError(msg)
        return self


# METHODS.md §1 anchors the FRP/catamaran row; the others scale from it by seakeeping.
CLASS_THRESHOLDS: dict[VesselClass, ClassThresholds] = {
    VesselClass.CATAMARAN: ClassThresholds(
        caution_wave_height_m=1.0,
        avoid_wave_height_m=1.5,
        caution_wind_speed_ms=7.0,
        avoid_wind_speed_ms=10.0,
        min_comfortable_swell_period_s=7.0,
        rationale=(
            "ASSUMPTION, not sourced. Reasoned from seakeeping: a traditional catamaran "
            "has minimal freeboard, no self-righting and usually no powered return, so it "
            "is held below the FRP vallam thresholds that METHODS.md §1 does source. "
            "Review with NIOT or a fisheries officer before operational use."
        ),
    ),
    VesselClass.FRP_VALLAM: ClassThresholds(
        caution_wave_height_m=1.5,
        avoid_wave_height_m=2.0,
        caution_wind_speed_ms=8.0,
        avoid_wind_speed_ms=12.0,
        min_comfortable_swell_period_s=6.0,
        rationale="METHODS.md §1: FRP vallam Hs < 1.5 m caution, < 2 m avoid.",
    ),
    VesselClass.GILLNETTER: ClassThresholds(
        caution_wave_height_m=2.0,
        avoid_wave_height_m=3.0,
        caution_wind_speed_ms=10.0,
        avoid_wind_speed_ms=15.0,
        min_comfortable_swell_period_s=5.0,
        rationale=(
            "ASSUMPTION, not sourced. Reasoned from seakeeping: a larger motorised hull "
            "with more freeboard and powered return sits between the FRP vallam and "
            "trawler rows that METHODS.md §1 does source. Review before operational use."
        ),
    ),
    VesselClass.MECHANIZED_TRAWLER: ClassThresholds(
        caution_wave_height_m=2.5,
        avoid_wave_height_m=4.0,
        caution_wind_speed_ms=13.0,
        avoid_wind_speed_ms=18.0,
        min_comfortable_swell_period_s=4.0,
        rationale=(
            "METHODS.md §1: trawler thresholds are higher. Decked hull, substantial "
            "freeboard, engine power to hold station or run for shelter."
        ),
    ),
}


class VesselProfile(OrcaModel):
    """One boat, as the safety kernel needs to know it.

    Stored per user in agent memory (PLAN.md Phase 5.7) so a fisherman states it once.
    """

    vessel_id: str
    vessel_class: VesselClass
    length_overall_m: float = Field(gt=0, le=60)
    engine_power_hp: float = Field(ge=0, description="0 for a non-motorised craft.")
    range_nm: float = Field(gt=0, description="Safe operating range from home port.")
    freeboard_m: float = Field(gt=0, description="Deck height above waterline when loaded.")
    crew_size: int = Field(ge=1)
    home_port: str
    equipment: frozenset[SafetyEquipment] = frozenset()
    threshold_overrides: ClassThresholds | None = Field(
        default=None,
        description="Set only with a stated reason; the class default applies otherwise.",
    )

    @property
    def thresholds(self) -> ClassThresholds:
        """The thresholds this boat is judged against."""
        return self.threshold_overrides or CLASS_THRESHOLDS[self.vessel_class]

    @property
    def is_motorised(self) -> bool:
        return self.engine_power_hp > 0

    @property
    def can_call_for_help(self) -> bool:
        """Whether the boat can raise an alarm if the forecast is wrong.

        Drives the confidence band, not the score: the sea does not care what radio you
        carry, but ORCA should be less willing to say "go" to a boat that cannot call.
        """
        return bool(
            self.equipment
            & {
                SafetyEquipment.VHF_RADIO,
                SafetyEquipment.EPIRB,
                SafetyEquipment.NAVIC_GEMINI,
            }
        )

    @property
    def margin_factor(self) -> float:
        """A 0-1 multiplier for how much margin this boat has beyond its class.

        Small and deliberately conservative: freeboard and power genuinely matter, but
        inflating a boat's rating on two numbers would be exactly the kind of false
        precision this system exists to avoid. Bounded to ±10%.
        """
        expected_freeboard = 0.08 * self.length_overall_m
        freeboard_ratio = self.freeboard_m / expected_freeboard if expected_freeboard else 1.0
        bounded = max(0.9, min(1.1, freeboard_ratio))
        if not self.is_motorised:
            bounded = min(bounded, 1.0)
        return bounded
