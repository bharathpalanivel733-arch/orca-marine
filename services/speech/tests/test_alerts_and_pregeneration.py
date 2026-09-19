"""Alert audio and build-time pre-generation (PLAN.md Phase 7.7, 7.9).

Together these two make the demo-day promise concrete: the rehearsed script and every
standard alert phrase are already synthesized, so they play with nothing reachable.

The tests also pin down the limits of that promise. A sentence about this morning's waves
was not pre-generated — it could not have been — and pretending otherwise would be the
kind of claim that collapses on stage.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orca_speech import (
    DEMO_SCRIPT,
    REHEARSED,
    AllProvidersFailedError,
    InMemoryAudioCache,
    Language,
    NotAnAlertTemplateError,
    ProviderTier,
    SynthesisRequest,
    TemplateId,
    TtsChain,
    alert_or_text_only,
    audit_script,
    build_default_chain,
    extract_numbers,
    pregenerate,
    scripted_texts,
    select_voice,
    synthesize_alert,
)
from orca_speech.providers import ProviderUnavailableError

NOW = datetime(2026, 9, 19, 5, 30, tzinfo=UTC)

CYCLONE_SLOTS = {
    "system_name": "Cyclone Fengal",
    "distance": "180 km",
    "location": "Nagapattinam",
    "bearing": "north-west",
    "speed": "12 km/h",
    "authority": "IMD",
    "issued_time": "05 30",
}


class WorkingTts:
    name = "stub_tts"
    tier = ProviderTier.PRIMARY

    def __init__(self) -> None:
        self.texts: list[str] = []

    def available(self) -> bool:
        return True

    def supports(self, language: Language) -> bool:
        return True

    def synthesize(self, request: SynthesisRequest) -> bytes:
        self.texts.append(request.text)
        return f"audio:{request.text}".encode()


class DeadTts:
    name = "dead_tts"
    tier = ProviderTier.PRIMARY

    def available(self) -> bool:
        return False

    def supports(self, language: Language) -> bool:
        return True

    def synthesize(self, request: SynthesisRequest) -> bytes:
        raise ProviderUnavailableError("everything is down")


class TestAlertAudio:
    def test_an_alert_is_spoken_in_the_recipients_language(self) -> None:
        audio = synthesize_alert(
            TemplateId.ALERT_CYCLONE,
            Language.TAMIL,
            CYCLONE_SLOTS,
            chain=TtsChain([WorkingTts()]),
            created_at=NOW,
        )

        assert audio.message.language is Language.TAMIL
        assert "புயல் எச்சரிக்கை" in audio.message.text
        assert audio.audio

    def test_the_spoken_and_written_warning_carry_the_same_numbers(self) -> None:
        """A recipient who reads and one who listens must get the same warning."""
        audio = synthesize_alert(
            TemplateId.ALERT_CYCLONE,
            Language.HINDI,
            CYCLONE_SLOTS,
            chain=TtsChain([WorkingTts()]),
            created_at=NOW,
        )

        assert extract_numbers(audio.spoken.text) == extract_numbers(audio.message.text)
        assert "180" in audio.spoken.text

    def test_units_are_expanded_before_the_synthesizer_sees_the_text(self) -> None:
        provider = WorkingTts()

        synthesize_alert(
            TemplateId.ALERT_HIGH_WAVE,
            Language.TAMIL,
            {
                "location": "Kanyakumari",
                "wave_height": "3.4 m",
                "valid_until": "18 00",
                "authority": "INCOIS",
            },
            chain=TtsChain([provider]),
            created_at=NOW,
        )

        assert "மீட்டர்" in provider.texts[0]
        assert " m " not in provider.texts[0]

    def test_the_push_payload_carries_both_readings_and_the_provenance(self) -> None:
        audio = synthesize_alert(
            TemplateId.ALERT_CYCLONE,
            Language.TAMIL,
            CYCLONE_SLOTS,
            chain=TtsChain([WorkingTts()]),
            created_at=NOW,
        )

        payload = audio.push_payload()

        assert payload["language"] == "ta"
        assert payload["text"] == audio.message.text
        assert payload["spoken_text"] == audio.spoken.text
        assert payload["template_id"] == "alert.cyclone"
        assert payload["audio_base64"]
        assert payload["translation_reviewed"] is False

    def test_audio_can_be_referenced_instead_of_inlined(self) -> None:
        """Inlining is right at sea and wrong on a metered link with a long clip."""
        audio = synthesize_alert(
            TemplateId.ALERT_CYCLONE,
            Language.TAMIL,
            CYCLONE_SLOTS,
            chain=TtsChain([WorkingTts()]),
            created_at=NOW,
        )

        payload = audio.push_payload(inline_audio=False)

        assert "audio_base64" not in payload
        assert payload["audio_cache_key"].endswith(".wav")

    def test_a_non_alert_template_cannot_be_pushed(self) -> None:
        """An alert interrupts someone at sea; only a warning earns that."""
        with pytest.raises(NotAnAlertTemplateError):
            synthesize_alert(
                TemplateId.ZONE_RECOMMENDATION,
                Language.TAMIL,
                {},
                chain=TtsChain([WorkingTts()]),
                created_at=NOW,
            )

    def test_a_pre_generated_alert_needs_no_provider_at_all(self) -> None:
        """The cyclone warning goes out with Bhashini down and no GPU box."""
        working = TtsChain([WorkingTts()])
        prepared = synthesize_alert(
            TemplateId.ALERT_CYCLONE,
            Language.TAMIL,
            CYCLONE_SLOTS,
            chain=working,
            created_at=NOW,
        )
        cache = InMemoryAudioCache({prepared.cache_key: prepared.audio})

        during_outage = synthesize_alert(
            TemplateId.ALERT_CYCLONE,
            Language.TAMIL,
            CYCLONE_SLOTS,
            chain=build_default_chain(cache=cache),
            created_at=NOW,
        )

        assert during_outage.cache_hit
        assert during_outage.audio == prepared.audio


class TestAlertDegradation:
    def test_the_warning_still_goes_out_as_text_when_no_audio_is_possible(self) -> None:
        """A cyclone alert is not worth withholding because a voice service is down."""
        payload = alert_or_text_only(
            TemplateId.ALERT_CYCLONE,
            Language.TAMIL,
            CYCLONE_SLOTS,
            chain=TtsChain([DeadTts()]),
            created_at=NOW,
        )

        assert payload["audio_available"] is False
        assert "புயல் எச்சரிக்கை" in payload["text"]
        assert payload["audio_failure"]

    def test_a_successful_alert_is_marked_available(self) -> None:
        payload = alert_or_text_only(
            TemplateId.ALERT_CYCLONE,
            Language.TAMIL,
            CYCLONE_SLOTS,
            chain=TtsChain([WorkingTts()]),
            created_at=NOW,
        )

        assert payload["audio_available"] is True

    def test_total_failure_raises_for_callers_that_want_to_know(self) -> None:
        with pytest.raises(AllProvidersFailedError):
            synthesize_alert(
                TemplateId.ALERT_CYCLONE,
                Language.TAMIL,
                CYCLONE_SLOTS,
                chain=TtsChain([DeadTts()]),
                created_at=NOW,
            )


class TestPreGeneration:
    def test_the_demo_script_renders_cleanly_in_every_rehearsed_language(self) -> None:
        """Catches a script line left behind when a template gained a slot."""
        assert audit_script() == ()

    @pytest.mark.parametrize("language", sorted(REHEARSED))
    def test_the_script_produces_one_normalized_string_per_line(self, language: Language) -> None:
        texts = scripted_texts(language)

        assert len(texts) == len(DEMO_SCRIPT)
        assert all(text.strip() for text in texts)

    def test_pre_generated_text_is_normalized_not_display_text(self) -> None:
        """Caching display text would fill the cache with keys nothing ever looks up."""
        texts = scripted_texts(Language.TAMIL)

        assert any("மீட்டர்" in text for text in texts)

    def test_pre_generation_fills_the_cache_for_every_rehearsed_language(self) -> None:
        cache = InMemoryAudioCache()

        reports = pregenerate(TtsChain([WorkingTts()]), cache)

        assert len(reports) == len(REHEARSED)
        assert all(report.complete for report in reports)
        assert len(cache) == len(DEMO_SCRIPT) * len(REHEARSED)

    def test_a_second_run_does_not_resynthesize(self) -> None:
        """Pre-generation is a build step; it must be cheap to re-run."""
        provider = WorkingTts()
        cache = InMemoryAudioCache()
        pregenerate(TtsChain([provider]), cache)
        first_pass = len(provider.texts)

        pregenerate(TtsChain([provider]), cache)

        assert len(provider.texts) == first_pass

    def test_the_cached_clips_are_exactly_what_the_live_path_will_ask_for(self) -> None:
        """The whole point: the pre-generation job and the live path share a key."""
        cache = InMemoryAudioCache()
        pregenerate(TtsChain([WorkingTts()]), cache)

        voice = select_voice(Language.TAMIL)
        text = scripted_texts(Language.TAMIL)[0]
        request = SynthesisRequest(text=text, language=Language.TAMIL, voice=voice)

        result = build_default_chain(cache=cache).synthesize(request)

        assert result.cache_hit

    def test_a_failed_phrase_is_reported_rather_than_silently_dropped(self) -> None:
        """Knowing beforehand which sentence needs a live provider is the point of a report."""
        reports = pregenerate(TtsChain([DeadTts()]), InMemoryAudioCache())

        assert all(not report.complete for report in reports)
        assert all(report.cached == 0 for report in reports)
        assert all(len(report.missing) == len(DEMO_SCRIPT) for report in reports)

    def test_coverage_reports_the_ratio_it_achieved(self) -> None:
        cache = InMemoryAudioCache()

        reports = pregenerate(TtsChain([WorkingTts()]), cache, languages=[Language.TAMIL])

        assert reports[0].ratio == 1.0
        assert reports[0].voice == "ta_female_1"
