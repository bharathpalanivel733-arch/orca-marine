"""Turning a fired trigger into words and audio (PLAN.md Phase 8.2, 8.3, 8.9).

The bridge between the deterministic rules and the Phase 7 speech layer. Two constraints
carry over unchanged, and both matter more here than in an answered question — an alert
arrives unrequested, and its recipient has no context to check it against:

* **No model writes any of this.** Each trigger maps to a fixed template id, and the slot
  values are formatted from the trigger's own observed numbers.
* **The numbers survive to the audio.** ``render_for_speech`` runs the numeric-preservation
  check, so a wave height cannot change between the CAP ``<description>`` and the voice.

The CAP text and the spoken text come from the *same* rendered message, which is what makes
"the alert the fisherman hears is the alert SACHET received" true by construction rather
than by review.
"""

from __future__ import annotations

from dataclasses import dataclass

from orca_speech import (
    Language,
    NormalizedSpeech,
    RenderedMessage,
    TemplateId,
    format_measurement,
    render_for_speech,
)

from orca_alerts.triggers import TriggerId, TriggerResult

# Which sentence each rule speaks. Fixed mapping: a trigger cannot choose its wording at
# runtime, so the set of things ORCA can say in an alert is enumerable and reviewable.
TEMPLATE_BY_TRIGGER: dict[TriggerId, TemplateId] = {
    TriggerId.CYCLONE_WIND: TemplateId.ALERT_CYCLONE,
    TriggerId.CYCLONE_CONE: TemplateId.ALERT_CYCLONE,
    TriggerId.WAVE_HEIGHT: TemplateId.ALERT_HIGH_WAVE,
    TriggerId.WIND_SPEED: TemplateId.ALERT_RETURN_TO_SHORE,
    TriggerId.LIGHTNING: TemplateId.ALERT_RETURN_TO_SHORE,
    TriggerId.SQUALL: TemplateId.ALERT_RETURN_TO_SHORE,
    TriggerId.GEOFENCE_PROXIMITY: TemplateId.GEOFENCE_APPROACHING,
    TriggerId.GEOFENCE_DRIFT: TemplateId.GEOFENCE_APPROACHING,
}

IMBL_NAME = "India-Sri Lanka maritime boundary"


class MissingAlertContextError(ValueError):
    """A slot a template needs was not supplied and cannot be derived from the trigger.

    Raised rather than filled with a placeholder. An alert reading "wave height —" is worse
    than a logged failure, because it looks like a delivered warning.
    """


@dataclass(frozen=True)
class AlertContext:
    """Everything a template might need that the trigger itself does not carry.

    Supplied by the scheduler from the subscriber and the hazard picture. Optional because
    different templates need different subsets; a missing one that *is* needed raises.
    """

    location_name: str = ""
    harbour_name: str = ""
    authority: str = "INCOIS"
    valid_until: str = ""
    issued_time: str = ""
    system_name: str = ""
    distance_text: str = ""
    bearing_text: str = ""
    speed_text: str = ""
    wave_height_text: str = ""
    wind_speed_text: str = ""
    safe_bearing_text: str = ""


@dataclass(frozen=True)
class AlertMessage:
    """The alert as text and as speech-ready text, in one language."""

    rendered: RenderedMessage
    spoken: NormalizedSpeech
    template_id: TemplateId

    @property
    def text(self) -> str:
        return self.rendered.text

    @property
    def language(self) -> Language:
        return self.rendered.language

    @property
    def headline(self) -> str:
        """A CAP headline: the first sentence, capped at CAP's recommended 160 characters.

        Truncating on a sentence boundary rather than mid-word matters — CAP consumers show
        the headline alone, and a clipped instruction can invert its meaning.
        """
        first = self.text.split(".")[0].strip()
        if not first:
            first = self.text.strip()
        return first if len(first) <= 160 else first[:157].rstrip() + "…"


def build_slots(
    result: TriggerResult, context: AlertContext
) -> dict[str, str]:
    """Slot values for a trigger's template, formatted from its observed numbers.

    Every numeric slot is derived from ``result.observed`` — the same values the rule
    compared against its threshold — so the sentence and the decision cannot disagree.
    """
    template_id = TEMPLATE_BY_TRIGGER[result.trigger_id]

    if template_id is TemplateId.ALERT_CYCLONE:
        return {
            "system_name": context.system_name or str(result.observed.get("system", "")),
            "distance": context.distance_text or _require(context, "distance_text"),
            "location": context.location_name or _require(context, "location_name"),
            "bearing": context.bearing_text or "unknown",
            "speed": context.speed_text or "unknown",
            "authority": context.authority,
            "issued_time": context.issued_time or _require(context, "issued_time"),
        }

    if template_id is TemplateId.ALERT_HIGH_WAVE:
        observed = result.observed.get("significant_wave_height_m")
        wave_text = (
            format_measurement(float(observed), "m")
            if isinstance(observed, int | float)
            else context.wave_height_text or _require(context, "wave_height_text")
        )
        return {
            "location": context.location_name or _require(context, "location_name"),
            "wave_height": wave_text,
            "valid_until": context.valid_until or _require(context, "valid_until"),
            "authority": context.authority,
        }

    if template_id is TemplateId.ALERT_RETURN_TO_SHORE:
        return {
            "location": context.location_name or _require(context, "location_name"),
            "wave_height": context.wave_height_text or "not reported",
            "wind_speed": _wind_text(result, context),
            "harbour": context.harbour_name or _require(context, "harbour_name"),
            "distance": context.distance_text or _require(context, "distance_text"),
        }

    # GEOFENCE_APPROACHING, for both the proximity and the drift rule.
    return {
        "distance": _boundary_distance_text(result, context),
        "boundary": IMBL_NAME,
        "time_to_boundary": _time_to_boundary_text(result),
    }


def _require(context: AlertContext, field_name: str) -> str:
    msg = f"alert context is missing {field_name}, which this template needs"
    raise MissingAlertContextError(msg)


def _wind_text(result: TriggerResult, context: AlertContext) -> str:
    observed = result.observed.get("wind_speed_ms")
    if isinstance(observed, int | float):
        return format_measurement(float(observed), "m/s")
    return context.wind_speed_text or "not reported"


def _boundary_distance_text(result: TriggerResult, context: AlertContext) -> str:
    observed = result.observed.get("distance_km")
    if isinstance(observed, int | float):
        return format_measurement(float(observed), "km")
    return context.distance_text or "unknown distance"


def _time_to_boundary_text(result: TriggerResult) -> str:
    """How long until the crossing, in words the template can carry.

    The proximity rule has no time component — it reports where a boat *is*, not where it is
    going — so it says so rather than inventing a figure.
    """
    minutes = result.observed.get("time_to_boundary_minutes")
    if isinstance(minutes, int | float):
        return f"{round(float(minutes))} min"
    return "an unknown time"


def build_message(
    result: TriggerResult,
    language: Language,
    context: AlertContext,
) -> AlertMessage:
    """Render a fired trigger into text and speech-ready text."""
    template_id = TEMPLATE_BY_TRIGGER[result.trigger_id]
    slots = build_slots(result, context)
    rendered, spoken = render_for_speech(template_id, language, slots)
    return AlertMessage(rendered=rendered, spoken=spoken, template_id=template_id)


def event_name(result: TriggerResult) -> str:
    """The CAP ``<event>`` string: a short, stable name for the hazard type.

    Stable because consumers group and filter on it; a wording change here reclassifies
    every historical alert of that kind.
    """
    return {
        TriggerId.CYCLONE_WIND: "Cyclone wind hazard",
        TriggerId.CYCLONE_CONE: "Cyclone forecast track",
        TriggerId.WAVE_HEIGHT: "High wave hazard",
        TriggerId.WIND_SPEED: "Strong wind hazard",
        TriggerId.LIGHTNING: "Lightning hazard",
        TriggerId.SQUALL: "Squall hazard",
        TriggerId.GEOFENCE_PROXIMITY: "Maritime boundary proximity",
        TriggerId.GEOFENCE_DRIFT: "Predicted maritime boundary crossing",
    }[result.trigger_id]
