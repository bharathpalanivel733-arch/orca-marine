"""Speech endpoints (PLAN.md Phase 7.2, 7.6).

``POST /api/v1/speech/transcribe`` and ``POST /api/v1/speech/synthesize``, plus a
capability listing the PWA reads at startup to build its language menu.

Both endpoints return the provider trail alongside the result. That is not diagnostics for
its own sake: audio carries no visible provenance, so a clip from the third fallback sounds
exactly as authoritative as one from the first, and the client needs to be able to say
"cached voice" or "offline voice" in the UI.

**No credentials are configured in this repository**, so in its default state the ASR chain
has only the browser-transcript provider and the TTS chain answers from the audio cache.
That is the documented outage behaviour (PLAN.md 7.7) rather than a broken deployment, and
``/api/v1/speech/capabilities`` reports which providers are actually configured so nobody
has to guess.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from orca_speech import (
    REHEARSED,
    AllProvidersFailedError,
    AsrChain,
    AudioCacheKey,
    AudioClip,
    BhashiniAsr,
    BhashiniTts,
    BrowserAsr,
    IndicParlerTts,
    IndicWhisperAsr,
    InMemoryAudioCache,
    Language,
    PreGeneratedTts,
    TtsChain,
    VoiceGender,
    detect_language,
    find_places_in,
    normalize_for_speech,
    profile_for,
    resolve_conversation_language,
    select_voice,
)
from pydantic import BaseModel, ConfigDict, Field

from orca_api.config import Settings, get_settings

router = APIRouter(prefix="/api/v1/speech", tags=["speech"])

# One process-wide cache. In development this is in-memory, which is honest about what it
# is: MinIO-backed caching arrives with the object-store wiring, and the interface is
# identical either way (orca_speech.ObjectStoreAudioCache).
_AUDIO_CACHE = InMemoryAudioCache()


class TranscribeRequest(BaseModel):
    """Audio to transcribe, as captured by the PWA."""

    model_config = ConfigDict(extra="forbid")

    audio_base64: str = Field(description="16 kHz mono WAV or Opus, base64-encoded.")
    encoding: str = "wav"
    sample_rate_hz: int = Field(default=16_000, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    language: Language | None = Field(
        default=None, description="Pin the language; omit to auto-detect."
    )
    preference: Language | None = Field(
        default=None, description="The user's stored language preference."
    )
    pinned_language: Language | None = Field(
        default=None, description="What this conversation has already settled on."
    )
    browser_transcript: str | None = Field(
        default=None,
        description="Web Speech API result from the client, used as the last-resort provider.",
    )


class ProviderAttemptOut(BaseModel):
    provider: str
    tier: str
    outcome: str
    detail: str | None = None


class PlaceOut(BaseModel):
    query: str
    resolution: str
    place_id: str | None = None
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    candidates: list[str] = Field(default_factory=list)


class TranscribeResponse(BaseModel):
    """``{text, lang, confidence, alternatives[]}`` plus provenance (PLAN.md 7.2)."""

    text: str
    lang: Language
    confidence: float
    alternatives: list[str] = Field(default_factory=list)
    detection_method: str
    code_mixed: bool
    needs_confirmation: bool
    reply_language: Language
    language_changed: bool
    places: list[PlaceOut] = Field(default_factory=list)
    provider: str
    tier: str
    attempts: list[ProviderAttemptOut] = Field(default_factory=list)


class SynthesizeRequest(BaseModel):
    """Text to speak. Must be a rendered template, not free text."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    language: Language
    gender: VoiceGender = VoiceGender.FEMALE
    speed: float = Field(default=1.0, ge=0.7, le=1.3)
    audio_format: str = "wav"


class SynthesizeResponse(BaseModel):
    """Audio plus the text that was actually spoken.

    ``spoken_text`` is returned so the client can show the visible transcript required by
    Phase 7.8 — and so a mismatch between what is displayed and what is said is visible
    rather than inaudible.
    """

    audio_base64: str
    content_type: str
    spoken_text: str
    language: Language
    voice: str
    speed: float
    cache_key: str
    cache_hit: bool
    provider: str
    tier: str
    degraded: bool
    attempts: list[ProviderAttemptOut] = Field(default_factory=list)


class VoiceOut(BaseModel):
    voice_id: str
    gender: str


class LanguageOut(BaseModel):
    code: Language
    english_name: str
    native_name: str
    rehearsed: bool
    voices: list[VoiceOut]


class CapabilitiesResponse(BaseModel):
    """What this deployment can actually do, rather than what the design allows."""

    languages: list[LanguageOut]
    asr_providers: list[str]
    tts_providers: list[str]
    bhashini_configured: bool
    self_hosted_models_available: bool
    cached_clips: int


def _settings() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(_settings)]


def build_asr_chain(settings: Settings, browser_transcript: str | None = None) -> AsrChain:
    """The documented ASR hierarchy for this deployment (PLAN.md §1.3).

    Providers are always constructed, never conditionally omitted: an unconfigured
    provider reporting itself unavailable is what produces the recorded attempt trail, and
    silently dropping it would hide the reason the primary was not used.
    """
    return AsrChain(
        [
            BhashiniAsr(
                api_key=settings.bhashini_ulca_api_key or None,
                user_id=settings.bhashini_user_id or None,
            ),
            IndicWhisperAsr(),
            BrowserAsr(browser_transcript),
        ]
    )


def build_tts_chain(settings: Settings) -> TtsChain:
    """The documented TTS hierarchy, cache first and cache last (PLAN.md §1.3, 7.7)."""
    return TtsChain(
        [
            BhashiniTts(
                api_key=settings.bhashini_ulca_api_key or None,
                user_id=settings.bhashini_user_id or None,
            ),
            IndicParlerTts(),
            PreGeneratedTts(_AUDIO_CACHE),
        ],
        cache=_AUDIO_CACHE,
    )


def _attempts(items: Any) -> list[ProviderAttemptOut]:
    return [
        ProviderAttemptOut(
            provider=a.provider, tier=a.tier.value, outcome=a.outcome.value, detail=a.detail
        )
        for a in items
    ]


@router.post(
    "/transcribe",
    response_model=TranscribeResponse,
    summary="Transcribe captured audio",
)
async def transcribe(request: TranscribeRequest, settings: SettingsDep) -> TranscribeResponse:
    """Turn audio into text, and decide which language to reply in.

    The transcript is untrusted input. It selects a template and resolves place names; it
    never reaches a numeric slot, and nothing in it is treated as an instruction.
    """
    try:
        audio = base64.b64decode(request.audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"audio_base64 is not valid base64: {exc}",
        ) from exc

    clip = AudioClip(
        data=audio,
        sample_rate_hz=request.sample_rate_hz,
        encoding=request.encoding,
        duration_seconds=request.duration_seconds,
    )
    chain = build_asr_chain(settings, request.browser_transcript)

    try:
        result = chain.transcribe(
            clip, language=request.language, preference=request.preference
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except AllProvidersFailedError as exc:
        # 503, not 500: nothing is broken, every provider is simply unreachable, and the
        # attempt trail says which and why.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "no speech recognizer is available",
                "attempts": [a.__dict__ for a in exc.attempts],
            },
        ) from exc

    detection = result.detection or detect_language(result.text, preference=request.preference)
    reply_language, changed = resolve_conversation_language(
        detection, pinned=request.pinned_language
    )

    places = [
        PlaceOut(
            query=resolution.query,
            resolution=resolution.kind.value,
            place_id=resolution.place.place_id if resolution.place else None,
            name=resolution.place.name if resolution.place else None,
            lat=resolution.place.lat if resolution.place else None,
            lon=resolution.place.lon if resolution.place else None,
            candidates=[p.name for p in resolution.candidates] if not resolution.place else [],
        )
        for resolution in find_places_in(result.text)
    ]

    return TranscribeResponse(
        text=result.text,
        lang=result.language,
        confidence=result.confidence,
        alternatives=[a.text for a in result.alternatives],
        detection_method=detection.method.value,
        code_mixed=detection.code_mixed,
        needs_confirmation=result.needs_confirmation,
        reply_language=reply_language,
        language_changed=changed,
        places=places,
        provider=result.provider,
        tier=result.tier.value,
        attempts=_attempts(result.attempts),
    )


@router.post(
    "/synthesize",
    response_model=SynthesizeResponse,
    summary="Speak a rendered answer",
)
async def synthesize(request: SynthesizeRequest, settings: SettingsDep) -> SynthesizeResponse:
    """Normalize, then synthesize.

    Normalization runs here rather than in the client so that units are expanded and the
    numeric-preservation check happens on every synthesis, whatever calls it.
    """
    try:
        spoken = normalize_for_speech(request.text, request.language)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        voice = select_voice(request.language, request.gender)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    from orca_speech import SynthesisRequest

    synthesis = SynthesisRequest(
        text=spoken.text,
        language=request.language,
        voice=voice,
        speed=request.speed,
        audio_format=request.audio_format,
    )
    try:
        result = build_tts_chain(settings).synthesize(synthesis)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except AllProvidersFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "no voice is available for this text",
                "spoken_text": spoken.text,
                "attempts": [a.__dict__ for a in exc.attempts],
            },
        ) from exc

    return SynthesizeResponse(
        audio_base64=base64.b64encode(result.audio).decode("ascii"),
        content_type=result.content_type,
        spoken_text=spoken.text,
        language=request.language,
        voice=voice.voice_id,
        speed=request.speed,
        cache_key=result.cache_key.object_key,
        cache_hit=result.cache_hit,
        provider=result.provider,
        tier=result.tier.value,
        degraded=result.degraded,
        attempts=_attempts(result.attempts),
    )


@router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    summary="Languages, voices and which providers are configured",
)
async def capabilities(settings: SettingsDep) -> CapabilitiesResponse:
    """What this deployment can do right now.

    Reports configured-ness rather than claiming capability: the PWA uses it to decide
    whether to offer the mic at all, and a demo operator uses it to see, before going on
    stage, that Bhashini is not in fact wired up.
    """
    from orca_speech import PROFILES, VOICES

    asr = build_asr_chain(settings)
    tts = build_tts_chain(settings)

    languages = [
        LanguageOut(
            code=code,
            english_name=profile_for(code).english_name,
            native_name=profile_for(code).native_name,
            rehearsed=code in REHEARSED,
            voices=[
                VoiceOut(voice_id=v.voice_id, gender=v.gender.value)
                for v in VOICES.get(code, {}).values()
            ],
        )
        for code in PROFILES
    ]

    # Checked per chain rather than over a merged list: ASR and TTS providers satisfy
    # different protocols, and a joined iterable has no common type to call through.
    self_hosted = any(p.available() for p in asr.providers if "indic" in p.name) or any(
        p.available() for p in tts.providers if "indic" in p.name
    )
    return CapabilitiesResponse(
        languages=languages,
        asr_providers=[p.name for p in asr.providers],
        tts_providers=[p.name for p in tts.providers],
        bhashini_configured=bool(settings.bhashini_ulca_api_key and settings.bhashini_user_id),
        self_hosted_models_available=self_hosted,
        cached_clips=len(_AUDIO_CACHE),
    )


@router.get("/cache/{object_key:path}", summary="Fetch a cached clip by key")
async def cached_clip(object_key: str) -> dict[str, Any]:
    """Serve a pre-generated clip directly.

    Lets a client that already knows the cache key — from an alert push payload — fetch the
    audio without a synthesis round trip.
    """
    for key_str in _AUDIO_CACHE.keys():  # noqa: SIM118 - a tuple of keys, not a mapping
        if key_str == object_key:
            language, voice, speed_tag, filename = object_key.split("/")
            digest, _, audio_format = filename.partition(".")
            key = AudioCacheKey(
                text_sha256=digest,
                language=Language(language),
                voice=voice,
                speed=float(speed_tag.replace("_", ".")),
                audio_format=audio_format,
            )
            found = _AUDIO_CACHE.get(key)
            if found is not None:
                return {
                    "audio_base64": base64.b64encode(found.audio).decode("ascii"),
                    "content_type": found.content_type,
                    "cache_key": object_key,
                    "served_at": datetime.now(UTC).isoformat(),
                }
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="clip not cached")
