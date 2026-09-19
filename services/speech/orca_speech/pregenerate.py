"""Build-time audio pre-generation (PLAN.md Phase 7.7).

The job that makes "the demo works with Bhashini fully down" a fact rather than a hope.

It renders a fixed set of sentences — the rehearsed demo script and every standard alert
phrase — in every language and voice, synthesizes each once, and writes it to the audio
cache under the same key the live path will compute. Nothing at runtime needs to know this
ran; the live path simply finds its clip already there.

The awkward part is that templates have slots, and a slot's value is a number from a
kernel, which is not known until someone asks. Two honest responses, both used here:

* **The demo script is pinned.** Its slot values are fixed in :data:`DEMO_SCRIPT` because a
  rehearsed demo says specific sentences with specific numbers. Those exact strings are
  cached and will hit.
* **Live answers about live conditions will miss the cache** on their first utterance, and
  that is correct — those numbers did not exist when this job ran. They fall through to a
  live provider, and the write-through in :class:`~orca_speech.tts.TtsChain` caches them
  for everyone after.

So the guarantee is precise, and worth stating precisely: **every rehearsed sentence and
every standard alert plays with no network at all.** A novel sentence about this morning's
waves needs a synthesizer, from any tier. Claiming more than that would be claiming the
cache can speak numbers it has never seen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from orca_speech.cache import AudioCache, AudioCacheKey, CoverageReport, has_unfilled_slots
from orca_speech.languages import REHEARSED, Language
from orca_speech.normalize import normalize_for_speech
from orca_speech.providers import AllProvidersFailedError
from orca_speech.templates import CATALOGUE, TemplateId, render
from orca_speech.tts import (
    DEFAULT_SPEED,
    SynthesisRequest,
    TtsChain,
    VoiceGender,
    select_voice,
)


@dataclass(frozen=True)
class ScriptedLine:
    """One sentence to pre-generate, with the slot values it is spoken with."""

    template_id: TemplateId
    slots: Mapping[str, str]
    note: str = ""


# The rehearsed demo (DEMO.md) plus the standard alert wordings. Slot values are the ones
# the demo actually says — including the abstention, which is rehearsed deliberately
# because refusing well is part of what ORCA is demonstrating.
DEMO_SCRIPT: tuple[ScriptedLine, ...] = (
    ScriptedLine(
        TemplateId.SAFETY_SAFE,
        {
            "vessel_class": "FRP vallam",
            "wave_height": "1.1 m",
            "wind_speed": "6.0 m/s",
            "score": "78",
            "data_age": "2.0 h",
        },
        note="opening question: is it safe this morning",
    ),
    ScriptedLine(
        TemplateId.SAFETY_AVOID,
        {
            "vessel_class": "FRP vallam",
            "wave_height": "2.8 m",
            "wind_speed": "14.0 m/s",
            "score": "21",
            "reason": "Wave height is above the limit for this boat class.",
        },
        note="deteriorating conditions",
    ),
    ScriptedLine(
        TemplateId.ABSTAIN_STALE,
        {"data_age": "15.0 h", "retry_after": "3.0 h"},
        note="the rehearsed refusal",
    ),
    ScriptedLine(
        TemplateId.ZONE_RECOMMENDATION,
        {
            "rank": "1",
            "bearing": "110 deg",
            "origin": "Rameswaram",
            "distance": "18.4 km",
            "travel_time": "1.4 h",
            "score": "74",
            "advisory_age": "9.0 h",
        },
        note="first option on the trade-off frontier",
    ),
    ScriptedLine(
        TemplateId.GEOFENCE_APPROACHING,
        {
            "distance": "4.2 km",
            "boundary": "India-Sri Lanka maritime boundary",
            "time_to_boundary": "22 min",
        },
        note="predictive drift warning",
    ),
    ScriptedLine(
        TemplateId.GEOFENCE_CROSSED,
        {
            "boundary": "India-Sri Lanka maritime boundary",
            "distance": "0.8 km",
            "safe_bearing": "290 deg",
        },
    ),
    ScriptedLine(
        TemplateId.ALERT_CYCLONE,
        {
            "system_name": "Cyclone Fengal",
            "distance": "180 km",
            "location": "Nagapattinam",
            "bearing": "north-west",
            "speed": "12 km/h",
            "authority": "IMD",
            "issued_time": "05 30",
        },
    ),
    ScriptedLine(
        TemplateId.ALERT_HIGH_WAVE,
        {
            "location": "Kanyakumari",
            "wave_height": "3.4 m",
            "valid_until": "18 00",
            "authority": "INCOIS",
        },
    ),
    ScriptedLine(
        TemplateId.ALERT_RETURN_TO_SHORE,
        {
            "location": "Palk Bay",
            "wave_height": "2.6 m",
            "wind_speed": "16.0 m/s",
            "harbour": "Rameswaram",
            "distance": "11.0 km",
        },
    ),
)


def scripted_texts(
    language: Language,
    *,
    script: Sequence[ScriptedLine] = DEMO_SCRIPT,
) -> tuple[str, ...]:
    """The normalized strings a language's cache must contain.

    Normalized, because that is what a synthesizer receives and therefore what the cache
    key hashes. Pre-generating the display text instead would produce keys nothing ever
    looks up — a cache that is full and never hit.
    """
    texts: list[str] = []
    for line in script:
        message = render(line.template_id, language, line.slots)
        spoken = normalize_for_speech(message.text, message.language)
        texts.append(spoken.text)
    return tuple(texts)


def pregenerate(
    chain: TtsChain,
    cache: AudioCache,
    *,
    languages: Iterable[Language] = REHEARSED,
    genders: Sequence[VoiceGender] = (VoiceGender.FEMALE,),
    speed: float = DEFAULT_SPEED,
    script: Sequence[ScriptedLine] = DEMO_SCRIPT,
    overwrite: bool = False,
) -> tuple[CoverageReport, ...]:
    """Synthesize and cache every scripted line. Returns coverage per language and voice.

    Failures are collected rather than raised. A run that manages nine of ten phrases is
    more useful than one that aborts on the first provider hiccup, and the report names
    exactly which sentence will need a live provider at demo time.
    """
    reports: list[CoverageReport] = []
    for language in languages:
        texts = scripted_texts(language, script=script)
        for gender in genders:
            try:
                voice = select_voice(language, gender)
            except LookupError:
                reports.append(
                    CoverageReport(
                        language=language,
                        voice=f"<none:{gender.value}>",
                        requested=len(texts),
                        cached=0,
                        missing=texts,
                    )
                )
                continue

            cached = 0
            missing: list[str] = []
            for text in texts:
                key = AudioCacheKey.for_text(
                    text, language=language, voice=voice.voice_id, speed=speed
                )
                if not overwrite and cache.has(key):
                    cached += 1
                    continue
                request = SynthesisRequest(
                    text=text, language=language, voice=voice, speed=speed
                )
                try:
                    result = chain.synthesize(request)
                except (AllProvidersFailedError, ValueError):
                    missing.append(text)
                    continue
                cache.put(key, result.audio, content_type=result.content_type)
                cached += 1

            reports.append(
                CoverageReport(
                    language=language,
                    voice=voice.voice_id,
                    requested=len(texts),
                    cached=cached,
                    missing=tuple(missing),
                )
            )
    return tuple(reports)


def audit_script(script: Sequence[ScriptedLine] = DEMO_SCRIPT) -> tuple[str, ...]:
    """Problems with the script itself, found without synthesizing anything.

    Catches the two mistakes that would otherwise surface as bad audio: a line naming a
    template that no longer exists, and a line whose rendered text still contains a brace
    because a template gained a slot the script was never updated for.
    """
    problems: list[str] = []
    for line in script:
        if line.template_id not in CATALOGUE:
            problems.append(f"{line.template_id.value}: not in the catalogue")
            continue
        for language in REHEARSED:
            try:
                message = render(line.template_id, language, line.slots)
            except KeyError as exc:
                problems.append(f"{line.template_id.value} [{language.value}]: {exc}")
                continue
            if has_unfilled_slots(message.text):
                problems.append(
                    f"{line.template_id.value} [{language.value}]: rendered text still has slots"
                )
    return tuple(problems)
