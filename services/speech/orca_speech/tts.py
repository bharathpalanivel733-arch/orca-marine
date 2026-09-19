"""Speech synthesis with a cache-first fallback chain (PLAN.md Phase 7.6, 7.7).

Order, and why:

0. **Cache.** Checked before any provider. Most of what ORCA says is one of a few hundred
   sentences, so the common path is a byte fetch — instant, free, and unaffected by
   anything being down.
1. **Bhashini TTS** — the government IndicTTS voices, best quality, quota-limited.
2. **Self-hosted Indic-Parler-TTS** — Apache-2.0, no quota, needs a GPU to be pleasant.
3. **Cache, strictly** — the pre-generated set again, as a final answer rather than an
   optimisation. If a sentence was pre-generated, it plays even with every network path
   dead; that is the promise in PLAN.md 7.7, and it is why this tier exists twice.

Putting the cache both first and last is deliberate and not a redundancy: the first check
is "have we said this before", and the last is "we cannot synthesize, is this one of the
sentences we prepared for exactly this situation". Between them sit the live providers.

**Nothing here writes text.** A synthesizer receives normalized template output and turns
it into audio. If the text is wrong, the fix is in :mod:`orca_speech.templates`, never in
a model asked to "say it more naturally" — that is the hop where a wave height would
change (:mod:`orca_speech.normalize`).

**Neither live provider is verified.** Bhashini needs a ULCA key this repository does not
hold; Indic-Parler-TTS needs weights that are not installed. Both report themselves
unavailable without them, and the tests exercise the resulting fallback — not the live
calls.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from orca_speech.cache import AudioCache, AudioCacheKey, has_unfilled_slots
from orca_speech.languages import PROFILES, Language, profile_for
from orca_speech.providers import (
    AllProvidersFailedError,
    ProviderAttempt,
    ProviderOutcome,
    ProviderTier,
    ProviderUnavailableError,
)

DEFAULT_SPEED = 1.0

# Speed bounds. Below 0.7 Indic voices slur; above 1.3 a safety instruction stops being
# intelligible to a listener on a boat with an engine running.
MIN_SPEED = 0.7
MAX_SPEED = 1.3


class VoiceGender(StrEnum):
    """Voice gender, configurable per user (PLAN.md §1.3)."""

    FEMALE = "female"
    MALE = "male"


@dataclass(frozen=True)
class Voice:
    """A named voice for one language."""

    voice_id: str
    language: Language
    gender: VoiceGender

    def __str__(self) -> str:
        return self.voice_id


# One female and one male voice per rehearsed language. The ids are ORCA-side names, not
# provider service ids: a provider swap must not change a cache key, or every pre-generated
# clip would be orphaned the first time Bhashini renames a service.
VOICES: dict[Language, dict[VoiceGender, Voice]] = {
    Language.ENGLISH: {
        VoiceGender.FEMALE: Voice("en_female_1", Language.ENGLISH, VoiceGender.FEMALE),
        VoiceGender.MALE: Voice("en_male_1", Language.ENGLISH, VoiceGender.MALE),
    },
    Language.TAMIL: {
        VoiceGender.FEMALE: Voice("ta_female_1", Language.TAMIL, VoiceGender.FEMALE),
        VoiceGender.MALE: Voice("ta_male_1", Language.TAMIL, VoiceGender.MALE),
    },
    Language.HINDI: {
        VoiceGender.FEMALE: Voice("hi_female_1", Language.HINDI, VoiceGender.FEMALE),
        VoiceGender.MALE: Voice("hi_male_1", Language.HINDI, VoiceGender.MALE),
    },
}

# The seven smoke-tested languages get a single default voice each (PLAN.md 7.10).
for _language in PROFILES:
    if _language not in VOICES:
        VOICES[_language] = {
            VoiceGender.FEMALE: Voice(f"{_language.value}_female_1", _language, VoiceGender.FEMALE)
        }


def select_voice(language: Language, gender: VoiceGender = VoiceGender.FEMALE) -> Voice:
    """Choose a voice, falling back to whatever exists for that language.

    Falling back on *gender* is acceptable — the listener still hears their own language.
    Falling back on *language* would not be, so a language with no voice raises.
    """
    voices = VOICES.get(language)
    if not voices:
        msg = f"no voice configured for {language.value}"
        raise LookupError(msg)
    return voices.get(gender) or next(iter(voices.values()))


@dataclass(frozen=True)
class SynthesisRequest:
    """What to say, in which voice, how fast."""

    text: str
    language: Language
    voice: Voice
    speed: float = DEFAULT_SPEED
    audio_format: str = "wav"

    def validate(self) -> None:
        if not self.text.strip():
            msg = "nothing to synthesize"
            raise ValueError(msg)
        if has_unfilled_slots(self.text):
            # Would otherwise be read aloud as "wave height open brace wave underscore..."
            msg = f"refusing to synthesize text with unfilled template slots: {self.text!r}"
            raise ValueError(msg)
        if not MIN_SPEED <= self.speed <= MAX_SPEED:
            msg = f"speed {self.speed} outside the intelligible range {MIN_SPEED}–{MAX_SPEED}"
            raise ValueError(msg)
        if self.voice.language != self.language:
            msg = (
                f"voice {self.voice.voice_id} speaks {self.voice.language.value}, "
                f"not {self.language.value}"
            )
            raise ValueError(msg)

    @property
    def cache_key(self) -> AudioCacheKey:
        return AudioCacheKey.for_text(
            self.text,
            language=self.language,
            voice=self.voice.voice_id,
            speed=self.speed,
            audio_format=self.audio_format,
        )


@dataclass(frozen=True)
class SynthesisResult:
    """Audio plus the trail that produced it."""

    audio: bytes
    request: SynthesisRequest
    provider: str
    tier: ProviderTier
    cache_hit: bool = False
    content_type: str = "audio/wav"
    attempts: tuple[ProviderAttempt, ...] = field(default_factory=tuple)

    @property
    def cache_key(self) -> AudioCacheKey:
        return self.request.cache_key

    @property
    def degraded(self) -> bool:
        """Whether this clip came from something other than the primary voice service."""
        return self.tier is not ProviderTier.PRIMARY and not self.cache_hit


@runtime_checkable
class TtsProvider(Protocol):
    """A speech synthesizer."""

    name: str
    tier: ProviderTier

    def available(self) -> bool: ...

    def supports(self, language: Language) -> bool: ...

    def synthesize(self, request: SynthesisRequest) -> bytes: ...


class BhashiniTts:
    """Bhashini ULCA TTS (IndicTTS voices) — primary. Not verified live."""

    name = "bhashini_tts"
    tier = ProviderTier.PRIMARY

    def __init__(
        self,
        *,
        api_key: str | None = None,
        user_id: str | None = None,
        endpoint: str = "https://dhruva-api.bhashini.gov.in/services/inference/pipeline",
        transport: Any | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._user_id = user_id
        self._endpoint = endpoint
        self._transport = transport
        self._timeout = timeout_seconds

    def available(self) -> bool:
        return bool(self._api_key and self._user_id and self._transport is not None)

    def supports(self, language: Language) -> bool:
        return language in PROFILES

    def build_request(self, request: SynthesisRequest) -> dict[str, Any]:
        """The ULCA TTS request body — verifiable offline, unlike the call itself."""
        return {
            "pipelineTasks": [
                {
                    "taskType": "tts",
                    "config": {
                        "language": {"sourceLanguage": profile_for(request.language).ulca_code},
                        "gender": request.voice.gender.value,
                        "samplingRate": 22050,
                    },
                }
            ],
            "inputData": {"input": [{"source": request.text}]},
        }

    def synthesize(self, request: SynthesisRequest) -> bytes:
        if not self.available():
            raise ProviderUnavailableError(
                "no ULCA credentials or transport configured", ProviderOutcome.UNAVAILABLE
            )
        assert self._transport is not None
        response = self._transport.post(
            self._endpoint,
            json=self.build_request(request),
            headers={"Authorization": self._api_key or "", "userID": self._user_id or ""},
            timeout=self._timeout,
        )
        return self.parse_response(response)

    def parse_response(self, response: Any) -> bytes:
        import base64
        import binascii

        status = getattr(response, "status_code", 200)
        if status == 429:
            raise ProviderUnavailableError("ULCA quota exceeded", ProviderOutcome.QUOTA_EXCEEDED)
        if status >= 400:
            raise ProviderUnavailableError(f"ULCA returned {status}", ProviderOutcome.ERROR)

        body = response.json() if hasattr(response, "json") else response
        try:
            encoded = body["pipelineResponse"][0]["audio"][0]["audioContent"]
            audio = base64.b64decode(encoded, validate=True)
        except (KeyError, IndexError, TypeError, binascii.Error) as exc:
            raise ProviderUnavailableError(
                f"unreadable ULCA audio response: {exc}", ProviderOutcome.ERROR
            ) from exc
        if not audio:
            raise ProviderUnavailableError("empty audio payload", ProviderOutcome.ERROR)
        return audio


class IndicParlerTts:
    """Self-hosted AI4Bharat Indic-Parler-TTS — fallback 1. Weights not installed here."""

    name = "indic_parler_tts"
    tier = ProviderTier.SELF_HOSTED

    def __init__(
        self,
        *,
        pipeline: Any | None = None,
        model_id: str = "ai4bharat/indic-parler-tts",
    ) -> None:
        self._pipeline = pipeline
        self._model_id = model_id

    def available(self) -> bool:
        return self._pipeline is not None

    def supports(self, language: Language) -> bool:
        return language in PROFILES

    def synthesize(self, request: SynthesisRequest) -> bytes:
        if not self.available():
            raise ProviderUnavailableError(
                f"{self._model_id} not loaded on this host", ProviderOutcome.UNAVAILABLE
            )
        assert self._pipeline is not None
        audio = self._pipeline(
            request.text,
            language=request.language.value,
            voice=request.voice.voice_id,
            speed=request.speed,
        )
        if not audio:
            raise ProviderUnavailableError("empty audio payload", ProviderOutcome.ERROR)
        return bytes(audio)


class PreGeneratedTts:
    """The pre-generated cache, used as a provider of last resort.

    Distinct from the chain's opening cache probe: that one is an optimisation, this one is
    the documented answer to "Bhashini is down and the GPU box is not up". It only ever
    serves clips the build-time job prepared, so it cannot invent audio for a sentence
    nobody anticipated — it either has the exact clip or it declines.
    """

    name = "pregenerated_cache"
    tier = ProviderTier.CACHE

    def __init__(self, cache: AudioCache) -> None:
        self._cache = cache

    def available(self) -> bool:
        return True

    def supports(self, language: Language) -> bool:
        return True

    def synthesize(self, request: SynthesisRequest) -> bytes:
        cached = self._cache.get(request.cache_key)
        if cached is None:
            raise ProviderUnavailableError(
                f"no pre-generated clip for {request.cache_key.object_key}",
                ProviderOutcome.UNAVAILABLE,
            )
        return cached.audio


class TtsChain:
    """Cache first, then each provider in order, recording every attempt."""

    def __init__(
        self, providers: Sequence[TtsProvider], *, cache: AudioCache | None = None
    ) -> None:
        if not providers:
            msg = "a TTS chain needs at least one provider"
            raise ValueError(msg)
        self._providers = tuple(providers)
        self._cache = cache

    @property
    def providers(self) -> tuple[TtsProvider, ...]:
        return self._providers

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        request.validate()
        attempts: list[ProviderAttempt] = []

        if self._cache is not None:
            cached = self._cache.get(request.cache_key)
            if cached is not None:
                attempts.append(
                    ProviderAttempt(
                        provider="audio_cache",
                        tier=ProviderTier.CACHE,
                        outcome=ProviderOutcome.SUCCESS,
                    )
                )
                return SynthesisResult(
                    audio=cached.audio,
                    request=request,
                    provider="audio_cache",
                    tier=ProviderTier.CACHE,
                    cache_hit=True,
                    content_type=cached.content_type,
                    attempts=tuple(attempts),
                )

        for provider in self._providers:
            if not provider.supports(request.language):
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        tier=provider.tier,
                        outcome=ProviderOutcome.UNSUPPORTED_LANGUAGE,
                        detail=f"does not support {request.language.value}",
                    )
                )
                continue

            started = time.perf_counter()
            try:
                audio = provider.synthesize(request)
            except ProviderUnavailableError as exc:
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        tier=provider.tier,
                        outcome=exc.outcome,
                        detail=exc.detail,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                )
                continue
            except Exception as exc:  # noqa: BLE001 - one bad provider must not end the chain
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        tier=provider.tier,
                        outcome=ProviderOutcome.ERROR,
                        detail=f"{type(exc).__name__}: {exc}",
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                )
                continue

            attempts.append(
                ProviderAttempt(
                    provider=provider.name,
                    tier=provider.tier,
                    outcome=ProviderOutcome.SUCCESS,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            )
            # Write through, so the second person to ask the same question costs nothing
            # and survives the provider going down between the two requests.
            if self._cache is not None and provider.tier is not ProviderTier.CACHE:
                self._cache.put(request.cache_key, audio)
            return SynthesisResult(
                audio=audio,
                request=request,
                provider=provider.name,
                tier=provider.tier,
                attempts=tuple(attempts),
            )

        raise AllProvidersFailedError(tuple(attempts))


def build_default_chain(
    *,
    cache: AudioCache | None = None,
    bhashini: BhashiniTts | None = None,
    parler: IndicParlerTts | None = None,
) -> TtsChain:
    """The documented hierarchy, assembled (PLAN.md §1.3).

    Constructed even when nothing is configured: an all-unavailable chain backed by a
    populated cache is exactly the outage scenario, and it still speaks.
    """
    providers: list[TtsProvider] = [bhashini or BhashiniTts(), parler or IndicParlerTts()]
    if cache is not None:
        providers.append(PreGeneratedTts(cache))
    return TtsChain(providers, cache=cache)
