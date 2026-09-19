"""Provider fallback for ASR and TTS (PLAN.md Phase 7.2, 7.6, 7.7).

The scenario these tests exist for is concrete: **Bhashini is down or out of quota during
the demo.** PDF risk #4. The documented answer is a hierarchy ending in a pre-generated
cache, and a documented answer nobody has exercised is a hope.

So the outage is simulated rather than described — every live provider unavailable, the
cache populated, and the assertion is that ORCA still speaks and still says which tier
spoke. Degraded output that announces its degradation is the standard ORCA holds
everywhere else (Phase 1.11); audio is where it is easiest to quietly break, because a
third-choice clip sounds as authoritative as a first-choice one.

No test here makes a network call. The live Bhashini and self-hosted paths are **not
verified** — both report themselves unavailable without credentials or weights, which is
precisely the branch under test.
"""

from __future__ import annotations

import pytest

from orca_speech import (
    AllProvidersFailedError,
    AsrChain,
    AsrResult,
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
    ProviderOutcome,
    ProviderTier,
    ProviderUnavailableError,
    SynthesisRequest,
    TtsChain,
    build_default_chain,
    select_voice,
)

CLIP = AudioClip(data=b"\x00\x01" * 800, duration_seconds=2.0)
SPOKEN = "அலை உயரம் 2.5 மீட்டர்"


class StubAsr:
    """An ASR provider with scripted behaviour."""

    def __init__(
        self,
        name: str,
        tier: ProviderTier,
        *,
        text: str | None = None,
        outcome: ProviderOutcome = ProviderOutcome.UNAVAILABLE,
        raises: Exception | None = None,
        languages: set[Language] | None = None,
    ) -> None:
        self.name = name
        self.tier = tier
        self._text = text
        self._outcome = outcome
        self._raises = raises
        self._languages = languages
        self.calls = 0

    def available(self) -> bool:
        return self._text is not None

    def supports(self, language: Language | None) -> bool:
        return self._languages is None or language is None or language in self._languages

    def transcribe(self, clip: AudioClip, *, language: Language | None) -> AsrResult:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        if self._text is None:
            raise ProviderUnavailableError(f"{self.name} is down", self._outcome)
        return AsrResult(
            text=self._text,
            language=language or Language.TAMIL,
            confidence=0.9,
            provider=self.name,
            tier=self.tier,
        )


class StubTts:
    def __init__(
        self,
        name: str,
        tier: ProviderTier,
        *,
        audio: bytes | None = None,
        outcome: ProviderOutcome = ProviderOutcome.UNAVAILABLE,
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self.tier = tier
        self._audio = audio
        self._outcome = outcome
        self._raises = raises
        self.calls = 0

    def available(self) -> bool:
        return self._audio is not None

    def supports(self, language: Language) -> bool:
        return True

    def synthesize(self, request: SynthesisRequest) -> bytes:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        if self._audio is None:
            raise ProviderUnavailableError(f"{self.name} is down", self._outcome)
        return self._audio


class TestAsrFallback:
    def test_the_primary_is_used_when_it_works(self) -> None:
        primary = StubAsr("bhashini", ProviderTier.PRIMARY, text="கடல் எப்படி")
        backup = StubAsr("indicwhisper", ProviderTier.SELF_HOSTED, text="something else")

        result = AsrChain([primary, backup]).transcribe(CLIP)

        assert result.provider == "bhashini"
        assert backup.calls == 0
        assert not result.degraded

    def test_a_quota_failure_falls_through_to_the_self_hosted_model(self) -> None:
        primary = StubAsr("bhashini", ProviderTier.PRIMARY, outcome=ProviderOutcome.QUOTA_EXCEEDED)
        backup = StubAsr("indicwhisper", ProviderTier.SELF_HOSTED, text="கடல் எப்படி")

        result = AsrChain([primary, backup]).transcribe(CLIP)

        assert result.provider == "indicwhisper"
        assert result.degraded
        assert result.attempts[0].outcome is ProviderOutcome.QUOTA_EXCEEDED

    def test_every_attempt_is_recorded_in_order(self) -> None:
        """A clip from the third provider must be identifiable as such."""
        chain = AsrChain(
            [
                StubAsr("bhashini", ProviderTier.PRIMARY),
                StubAsr("indicwhisper", ProviderTier.SELF_HOSTED),
                StubAsr("browser", ProviderTier.CLIENT, text="wave height"),
            ]
        )

        result = chain.transcribe(CLIP)

        assert [a.provider for a in result.attempts] == ["bhashini", "indicwhisper", "browser"]
        assert [a.succeeded for a in result.attempts] == [False, False, True]

    def test_an_unexpected_exception_does_not_end_the_chain(self) -> None:
        """A provider fault is a reason to try the next one, not to stop speaking."""
        chain = AsrChain(
            [
                StubAsr("bhashini", ProviderTier.PRIMARY, raises=RuntimeError("socket reset")),
                StubAsr("indicwhisper", ProviderTier.SELF_HOSTED, text="கடல்"),
            ]
        )

        result = chain.transcribe(CLIP)

        assert result.provider == "indicwhisper"
        assert result.attempts[0].outcome is ProviderOutcome.ERROR
        assert "socket reset" in (result.attempts[0].detail or "")

    def test_a_provider_that_cannot_handle_the_language_is_skipped(self) -> None:
        chain = AsrChain(
            [
                StubAsr("browser", ProviderTier.CLIENT, text="x", languages={Language.ENGLISH}),
                StubAsr("indicwhisper", ProviderTier.SELF_HOSTED, text="கடல்"),
            ]
        )

        result = chain.transcribe(CLIP, language=Language.TAMIL)

        assert result.provider == "indicwhisper"
        assert result.attempts[0].outcome is ProviderOutcome.UNSUPPORTED_LANGUAGE

    def test_total_failure_reports_every_reason(self) -> None:
        """"Speech is unavailable" helps nobody; the reasons are what gets it fixed."""
        chain = AsrChain(
            [
                StubAsr("bhashini", ProviderTier.PRIMARY, outcome=ProviderOutcome.QUOTA_EXCEEDED),
                StubAsr("indicwhisper", ProviderTier.SELF_HOSTED),
            ]
        )

        with pytest.raises(AllProvidersFailedError) as exc:
            chain.transcribe(CLIP)

        assert len(exc.value.attempts) == 2
        assert "quota_exceeded" in str(exc.value)

    def test_a_low_confidence_transcript_asks_for_confirmation(self) -> None:
        """Acting on a misheard place name answers about a different stretch of sea."""
        result = AsrChain([BrowserAsr("wave height today", Language.ENGLISH)]).transcribe(
            CLIP, language=Language.ENGLISH
        )

        assert result.needs_confirmation


class TestUnconfiguredProvidersDegrade:
    """Without credentials or weights, each provider must decline rather than crash."""

    def test_bhashini_asr_without_credentials_is_unavailable(self) -> None:
        assert not BhashiniAsr().available()

    def test_bhashini_tts_without_credentials_is_unavailable(self) -> None:
        assert not BhashiniTts().available()

    def test_indicwhisper_without_weights_is_unavailable(self) -> None:
        assert not IndicWhisperAsr().available()

    def test_indic_parler_without_weights_is_unavailable(self) -> None:
        assert not IndicParlerTts().available()

    def test_an_unconfigured_provider_raises_the_recorded_failure_type(self) -> None:
        with pytest.raises(ProviderUnavailableError) as exc:
            BhashiniAsr().transcribe(CLIP, language=Language.TAMIL)

        assert exc.value.outcome is ProviderOutcome.UNAVAILABLE


class TestTtsFallback:
    def test_the_cache_is_checked_before_any_provider(self) -> None:
        """The common path costs no quota and no network."""
        voice = select_voice(Language.TAMIL)
        cache = InMemoryAudioCache()
        request = SynthesisRequest(text=SPOKEN, language=Language.TAMIL, voice=voice)
        cache.put(request.cache_key, b"cached-audio")
        primary = StubTts("bhashini", ProviderTier.PRIMARY, audio=b"live-audio")

        result = TtsChain([primary], cache=cache).synthesize(request)

        assert result.audio == b"cached-audio"
        assert result.cache_hit
        assert primary.calls == 0

    def test_a_miss_falls_through_to_the_primary_and_writes_through(self) -> None:
        """The second person to ask costs nothing, even if Bhashini dies in between."""
        voice = select_voice(Language.TAMIL)
        cache = InMemoryAudioCache()
        request = SynthesisRequest(text=SPOKEN, language=Language.TAMIL, voice=voice)
        primary = StubTts("bhashini", ProviderTier.PRIMARY, audio=b"live-audio")

        result = TtsChain([primary], cache=cache).synthesize(request)

        assert result.audio == b"live-audio"
        assert not result.cache_hit
        assert cache.has(request.cache_key)

    def test_the_documented_hierarchy_is_followed_in_order(self) -> None:
        voice = select_voice(Language.TAMIL)
        request = SynthesisRequest(text=SPOKEN, language=Language.TAMIL, voice=voice)
        bhashini = StubTts("bhashini", ProviderTier.PRIMARY, outcome=ProviderOutcome.QUOTA_EXCEEDED)
        parler = StubTts("parler", ProviderTier.SELF_HOSTED, audio=b"parler-audio")

        result = TtsChain([bhashini, parler]).synthesize(request)

        assert result.provider == "parler"
        assert result.tier is ProviderTier.SELF_HOSTED
        assert result.degraded

    def test_a_full_bhashini_outage_still_speaks_from_the_pre_generated_cache(self) -> None:
        """The scenario this whole design exists for (PLAN.md 7.7, PDF risk 4).

        Every live provider is down and the network is irrelevant: the rehearsed sentence
        was pre-generated, so it plays.
        """
        voice = select_voice(Language.TAMIL)
        request = SynthesisRequest(text=SPOKEN, language=Language.TAMIL, voice=voice)
        cache = InMemoryAudioCache({request.cache_key: b"pregenerated-audio"})
        chain = build_default_chain(cache=cache)

        result = chain.synthesize(request)

        assert result.audio == b"pregenerated-audio"
        assert result.cache_hit

    def test_the_last_resort_cache_tier_is_reached_after_every_provider_fails(self) -> None:
        """Distinct from the opening probe: this is the cache answering, not optimising."""
        voice = select_voice(Language.TAMIL)
        request = SynthesisRequest(text=SPOKEN, language=Language.TAMIL, voice=voice)
        pregenerated = InMemoryAudioCache({request.cache_key: b"pregenerated-audio"})
        chain = TtsChain(
            [
                StubTts("bhashini", ProviderTier.PRIMARY),
                StubTts("parler", ProviderTier.SELF_HOSTED),
                PreGeneratedTts(pregenerated),
            ]
        )

        result = chain.synthesize(request)

        assert result.provider == "pregenerated_cache"
        assert result.tier is ProviderTier.CACHE
        assert [a.provider for a in result.attempts] == ["bhashini", "parler", "pregenerated_cache"]

    def test_an_unprepared_sentence_with_everything_down_fails_loudly(self) -> None:
        """Honest limit: the cache cannot speak a sentence nobody anticipated."""
        voice = select_voice(Language.TAMIL)
        request = SynthesisRequest(
            text="a sentence nobody rehearsed", language=Language.TAMIL, voice=voice
        )
        chain = build_default_chain(cache=InMemoryAudioCache())

        with pytest.raises(AllProvidersFailedError) as exc:
            chain.synthesize(request)

        assert [a.provider for a in exc.value.attempts] == [
            "bhashini_tts",
            "indic_parler_tts",
            "pregenerated_cache",
        ]


class TestSynthesisGuards:
    def test_text_with_unfilled_slots_is_refused(self) -> None:
        """Otherwise the voice reads "open brace wave underscore height" to a fisherman."""
        request = SynthesisRequest(
            text="wave height {wave_height}",
            language=Language.TAMIL,
            voice=select_voice(Language.TAMIL),
        )

        with pytest.raises(ValueError, match="unfilled template slots"):
            request.validate()

    def test_a_voice_from_the_wrong_language_is_refused(self) -> None:
        """A Hindi voice reading Tamil text is worse than a handled failure."""
        request = SynthesisRequest(
            text=SPOKEN, language=Language.TAMIL, voice=select_voice(Language.HINDI)
        )

        with pytest.raises(ValueError, match="speaks hi"):
            request.validate()

    def test_an_unintelligible_speed_is_refused(self) -> None:
        request = SynthesisRequest(
            text=SPOKEN, language=Language.TAMIL, voice=select_voice(Language.TAMIL), speed=2.5
        )

        with pytest.raises(ValueError, match="intelligible range"):
            request.validate()

    def test_every_language_has_a_voice(self) -> None:
        for language in Language:
            assert select_voice(language).language is language


class TestAudioClipLimits:
    def test_an_over_long_clip_is_refused_server_side(self) -> None:
        """A client can be modified; the 15 s cap is enforced here too."""
        with pytest.raises(ValueError, match="above the 15.0 s cap"):
            AudioClip(data=b"\x00" * 100, duration_seconds=22.0).validate()

    def test_an_empty_clip_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            AudioClip(data=b"").validate()

    def test_stereo_is_refused(self) -> None:
        with pytest.raises(ValueError, match="mono"):
            AudioClip(data=b"\x00\x01", channels=2).validate()

    def test_an_oversized_payload_is_refused_before_any_provider_is_called(self) -> None:
        provider = StubAsr("bhashini", ProviderTier.PRIMARY, text="x")

        with pytest.raises(ValueError, match="above the"):
            AsrChain([provider]).transcribe(AudioClip(data=b"\x00" * 2_000_000))

        assert provider.calls == 0


class TestBhashiniRequestShapes:
    """What can honestly be verified offline: the request bodies and response parsing."""

    def test_the_asr_request_carries_the_ulca_language_code(self) -> None:
        payload = BhashiniAsr().build_request(CLIP, language=Language.TAMIL)

        task = payload["pipelineTasks"][0]
        assert task["taskType"] == "asr"
        assert task["config"]["language"]["sourceLanguage"] == "ta"
        assert task["config"]["samplingRate"] == 16_000

    def test_the_tts_request_carries_the_voice_gender(self) -> None:
        request = SynthesisRequest(
            text=SPOKEN, language=Language.TAMIL, voice=select_voice(Language.TAMIL)
        )

        payload = BhashiniTts().build_request(request)

        task = payload["pipelineTasks"][0]
        assert task["taskType"] == "tts"
        assert task["config"]["language"]["sourceLanguage"] == "ta"
        assert task["config"]["gender"] == "female"

    def test_a_429_is_read_as_a_quota_failure_not_a_generic_error(self) -> None:
        """Quota exhaustion is the expected demo-day failure and deserves its own outcome."""

        class Response:
            status_code = 429

        with pytest.raises(ProviderUnavailableError) as exc:
            BhashiniTts().parse_response(Response())

        assert exc.value.outcome is ProviderOutcome.QUOTA_EXCEEDED

    def test_an_empty_transcript_is_a_failure_not_an_empty_answer(self) -> None:
        """Returning "" would send ORCA on to answer a question nobody asked."""

        class Response:
            @staticmethod
            def json() -> dict[str, object]:
                return {"pipelineResponse": [{"output": [{"source": "  "}]}]}

        with pytest.raises(ProviderUnavailableError):
            BhashiniAsr().parse_response(Response(), requested=Language.TAMIL)


def test_a_chain_needs_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        AsrChain([])


def test_an_unused_cache_key_helper_stays_consistent() -> None:
    """Guards against the chain and the pre-generation job computing keys differently."""
    voice = select_voice(Language.TAMIL)
    request = SynthesisRequest(text=SPOKEN, language=Language.TAMIL, voice=voice)

    assert request.cache_key == AudioCacheKey.for_text(
        SPOKEN, language=Language.TAMIL, voice=voice.voice_id, speed=1.0
    )
