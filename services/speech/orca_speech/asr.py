"""Speech recognition with a documented fallback chain (PLAN.md Phase 7.2).

Order, and why it is that order:

1. **Bhashini ULCA ASR** — the government pipeline, best Indic accuracy, and the one whose
   quota can vanish mid-demo. Tried first because when it works it is the right answer.
2. **Self-hosted IndicWhisper** — Apache-2.0, runs on the app server, no quota, slower.
   Independent of Bhashini's availability, which is the whole point of having it.
3. **Browser Web Speech API** — the transcript the client already produced. Weakest and
   English/Hindi only, so it is last; but a weak transcript the user can see and correct
   beats a spinner.

**No provider is verified live.** Bhashini requires a ULCA key this repository does not
have, and IndicWhisper requires model weights that are not installed. Both classes are
implemented against their documented request shapes and both report themselves
unavailable without credentials, which is what the tests exercise. `PROGRESS.md` records
this as unverified rather than as working.

A transcript is untrusted text. It is never used to fill a numeric slot, never translated
by a model, and never executed as an instruction — it selects a template and resolves a
place name (:mod:`orca_speech.gazetteer`), and the numbers come from kernels.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from orca_speech.detect import (
    DetectionMethod,
    LanguageDetection,
    detect_language,
)
from orca_speech.languages import PROFILES, Language, profile_for
from orca_speech.providers import (
    AllProvidersFailedError,
    ProviderAttempt,
    ProviderOutcome,
    ProviderTier,
    ProviderUnavailableError,
)

# Phase 7.1 caps capture at 15 s. Enforced server-side too: a client can be modified, and
# an unbounded upload is both a cost and a denial-of-service surface.
MAX_AUDIO_SECONDS = 15.0
MAX_AUDIO_BYTES = 16_000 * 2 * int(MAX_AUDIO_SECONDS) * 2  # 16 kHz mono PCM16, x2 headroom

# Below this, the UI asks the user to confirm the transcript before ORCA acts on it.
# Acting on a misheard place name is how a safety answer ends up describing a different
# stretch of sea entirely.
CONFIRM_BELOW_CONFIDENCE = 0.6


@dataclass(frozen=True)
class AudioClip:
    """Captured audio, with the format facts a provider needs."""

    data: bytes
    sample_rate_hz: int = 16_000
    channels: int = 1
    encoding: str = "wav"
    duration_seconds: float | None = None

    def validate(self) -> None:
        """Reject clips that cannot be transcribed, before any provider is called."""
        if not self.data:
            msg = "audio clip is empty"
            raise ValueError(msg)
        if len(self.data) > MAX_AUDIO_BYTES:
            msg = f"audio clip is {len(self.data)} bytes, above the {MAX_AUDIO_BYTES} limit"
            raise ValueError(msg)
        if self.duration_seconds is not None and self.duration_seconds > MAX_AUDIO_SECONDS:
            msg = (
                f"audio clip is {self.duration_seconds:.1f} s, "
                f"above the {MAX_AUDIO_SECONDS} s cap"
            )
            raise ValueError(msg)
        if self.channels != 1:
            msg = f"expected mono audio, got {self.channels} channels"
            raise ValueError(msg)


@dataclass(frozen=True)
class Transcript:
    """One candidate reading of the audio."""

    text: str
    confidence: float


@dataclass(frozen=True)
class AsrResult:
    """What was heard, in which language, and by whom.

    Matches the shape PLAN.md 7.2 specifies — ``{text, lang, confidence, alternatives[]}``
    — with the provider trail added, because a transcript from the third fallback deserves
    to be identifiable as such.
    """

    text: str
    language: Language
    confidence: float
    alternatives: tuple[Transcript, ...] = ()
    detection: LanguageDetection | None = None
    provider: str = ""
    tier: ProviderTier = ProviderTier.PRIMARY
    attempts: tuple[ProviderAttempt, ...] = field(default_factory=tuple)

    @property
    def needs_confirmation(self) -> bool:
        return self.confidence < CONFIRM_BELOW_CONFIDENCE

    @property
    def degraded(self) -> bool:
        return self.tier is not ProviderTier.PRIMARY


@runtime_checkable
class AsrProvider(Protocol):
    """A speech recognizer."""

    name: str
    tier: ProviderTier

    def available(self) -> bool: ...

    def supports(self, language: Language | None) -> bool: ...

    def transcribe(self, clip: AudioClip, *, language: Language | None) -> AsrResult: ...


class BhashiniAsr:
    """Bhashini ULCA ASR (IndicConformer) — primary.

    **Not verified against the live service.** Construction without a key yields a
    provider that reports itself unavailable, so the chain falls through exactly as it
    would during a quota outage. That path *is* tested; the live path is not.
    """

    name = "bhashini_asr"
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

    def supports(self, language: Language | None) -> bool:
        # Bhashini covers every language ORCA declares; auto-detect (None) is also valid,
        # since the ULCA pipeline can run language detection itself.
        return language is None or language in PROFILES

    def transcribe(self, clip: AudioClip, *, language: Language | None) -> AsrResult:
        if not self.available():
            raise ProviderUnavailableError(
                "no ULCA credentials or transport configured", ProviderOutcome.UNAVAILABLE
            )
        assert self._transport is not None
        payload = self.build_request(clip, language=language)
        response = self._transport.post(
            self._endpoint,
            json=payload,
            headers={"Authorization": self._api_key or "", "userID": self._user_id or ""},
            timeout=self._timeout,
        )
        return self.parse_response(response, requested=language)

    def build_request(self, clip: AudioClip, *, language: Language | None) -> dict[str, Any]:
        """The ULCA pipeline request body.

        Kept as a separate method so its shape is testable without a network call or a
        key — the part of this integration that can honestly be verified offline.
        """
        import base64

        config: dict[str, Any] = {
            "serviceId": "",
            "audioFormat": clip.encoding,
            "samplingRate": clip.sample_rate_hz,
        }
        if language is not None:
            config["language"] = {"sourceLanguage": profile_for(language).ulca_code}
        return {
            "pipelineTasks": [{"taskType": "asr", "config": config}],
            "inputData": {
                "audio": [{"audioContent": base64.b64encode(clip.data).decode("ascii")}]
            },
        }

    def parse_response(self, response: Any, *, requested: Language | None) -> AsrResult:
        """Read a ULCA response into an :class:`AsrResult`.

        A malformed or empty response is a provider failure, not an empty transcript:
        silently returning "" would send ORCA on to answer a question nobody asked.
        """
        body = response.json() if hasattr(response, "json") else response
        try:
            output = body["pipelineResponse"][0]["output"][0]
            text = str(output["source"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailableError(
                f"unreadable ULCA response: {exc}", ProviderOutcome.ERROR
            ) from exc
        if not text:
            raise ProviderUnavailableError("empty transcript", ProviderOutcome.ERROR)

        detection = detect_language(text, preference=requested)
        return AsrResult(
            text=text,
            language=requested or detection.language,
            confidence=float(output.get("confidence", 0.9)),
            detection=detection,
            provider=self.name,
            tier=self.tier,
        )


class IndicWhisperAsr:
    """Self-hosted AI4Bharat IndicWhisper / IndicWav2Vec — fallback 1.

    **Weights are not installed in this repository.** ``available()`` checks for the model
    runtime and returns False without it, so the chain behaves correctly on a machine that
    has never downloaded a multi-gigabyte checkpoint — including CI.
    """

    name = "indicwhisper"
    tier = ProviderTier.SELF_HOSTED

    def __init__(
        self, *, pipeline: Any | None = None, model_id: str = "ai4bharat/indicwhisper"
    ) -> None:
        self._pipeline = pipeline
        self._model_id = model_id

    def available(self) -> bool:
        return self._pipeline is not None

    def supports(self, language: Language | None) -> bool:
        return True

    def transcribe(self, clip: AudioClip, *, language: Language | None) -> AsrResult:
        if not self.available():
            raise ProviderUnavailableError(
                f"{self._model_id} not loaded on this host", ProviderOutcome.UNAVAILABLE
            )
        assert self._pipeline is not None
        output = self._pipeline(clip.data, language=language.value if language else None)
        text = str(output.get("text", "")).strip()
        if not text:
            raise ProviderUnavailableError("empty transcript", ProviderOutcome.ERROR)
        detection = detect_language(text, preference=language)
        return AsrResult(
            text=text,
            language=language or detection.language,
            confidence=float(output.get("confidence", 0.7)),
            detection=detection,
            provider=self.name,
            tier=self.tier,
        )


class BrowserAsr:
    """The Web Speech API transcript the client already has — last resort.

    Not a recognizer: it accepts a transcript produced in the browser. Confidence is
    capped low on purpose. Chrome's API is English- and Hindi-biased and will happily
    return a fluent-sounding misreading of Tamil, so ORCA treats its output as something
    to show the user for confirmation rather than something to act on.
    """

    name = "browser_web_speech"
    tier = ProviderTier.CLIENT
    MAX_CONFIDENCE = 0.5

    def __init__(
        self,
        client_transcript: str | None = None,
        client_language: Language | None = None,
    ) -> None:
        self._transcript = (client_transcript or "").strip()
        self._language = client_language

    def available(self) -> bool:
        return bool(self._transcript)

    def supports(self, language: Language | None) -> bool:
        return language in {None, Language.ENGLISH, Language.HINDI}

    def transcribe(self, clip: AudioClip, *, language: Language | None) -> AsrResult:
        if not self.available():
            raise ProviderUnavailableError(
                "no client-side transcript supplied", ProviderOutcome.UNAVAILABLE
            )
        detection = detect_language(self._transcript, preference=language or self._language)
        return AsrResult(
            text=self._transcript,
            language=language or self._language or detection.language,
            confidence=self.MAX_CONFIDENCE,
            detection=detection,
            provider=self.name,
            tier=self.tier,
        )


class AsrChain:
    """Try each provider in order; record every attempt.

    The chain never reorders itself, retries adaptively or "learns" a preference. A fixed
    order means the same outage produces the same behaviour every time, which is what
    makes the fallback testable and what makes a demo rehearsable.
    """

    def __init__(self, providers: Sequence[AsrProvider]) -> None:
        if not providers:
            msg = "an ASR chain needs at least one provider"
            raise ValueError(msg)
        self._providers = tuple(providers)

    @property
    def providers(self) -> tuple[AsrProvider, ...]:
        return self._providers

    def transcribe(
        self,
        clip: AudioClip,
        *,
        language: Language | None = None,
        preference: Language | None = None,
    ) -> AsrResult:
        """Transcribe, falling through the chain until something answers."""
        clip.validate()
        attempts: list[ProviderAttempt] = []

        for provider in self._providers:
            if not provider.supports(language):
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        tier=provider.tier,
                        outcome=ProviderOutcome.UNSUPPORTED_LANGUAGE,
                        detail=f"does not support {language.value if language else 'auto'}",
                    )
                )
                continue

            started = time.perf_counter()
            try:
                result = provider.transcribe(clip, language=language)
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
            except Exception as exc:  # noqa: BLE001 - a provider fault must not end the chain
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
            detection = result.detection or detect_language(result.text, preference=preference)
            resolved = language or detection.language
            return AsrResult(
                text=result.text,
                language=resolved,
                confidence=result.confidence,
                alternatives=result.alternatives,
                detection=_with_provider_method(detection, result, language),
                provider=result.provider or provider.name,
                tier=provider.tier,
                attempts=tuple(attempts),
            )

        raise AllProvidersFailedError(tuple(attempts))


def _with_provider_method(
    detection: LanguageDetection, result: AsrResult, requested: Language | None
) -> LanguageDetection:
    """Mark a detection as provider-supplied when the caller pinned the language.

    Keeps the method field honest: if the language was given rather than inferred, the
    provenance should not claim the script detector decided it.
    """
    if requested is None:
        return detection
    return LanguageDetection(
        language=requested,
        method=DetectionMethod.PROVIDER,
        confidence=max(detection.confidence, result.confidence),
        code_mixed=detection.code_mixed,
        secondary=detection.secondary,
        shares=detection.shares,
        matched_markers=detection.matched_markers,
    )
