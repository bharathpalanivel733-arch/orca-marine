"""Speech endpoints (PLAN.md Phase 7.2, 7.6).

These run against the API as this repository actually configures it: **no Bhashini
credentials and no self-hosted weights.** That is not a limitation of the tests, it is the
outage scenario the fallback hierarchy exists for, and it is the state the demo machine
will be in unless a key turns up.

So the assertions are about degrading correctly and saying so — a 503 that names every
provider it tried, a cached clip served with no provider at all, and a capabilities
endpoint that admits Bhashini is not wired up rather than implying it is.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from orca_speech import (
    AudioCacheKey,
    Language,
    normalize_for_speech,
    scripted_texts,
    select_voice,
)

from orca_api.main import create_app
from orca_api.routers import speech as speech_router

AUDIO = base64.b64encode(b"\x00\x01" * 800).decode("ascii")


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_cache():
    """Each test starts with an empty audio cache and leaves one behind."""
    speech_router._AUDIO_CACHE = type(speech_router._AUDIO_CACHE)()
    yield
    speech_router._AUDIO_CACHE = type(speech_router._AUDIO_CACHE)()


class TestTranscribe:
    def test_the_browser_transcript_is_used_when_nothing_else_is_configured(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/speech/transcribe",
            json={
                "audio_base64": AUDIO,
                "duration_seconds": 2.0,
                "browser_transcript": "is it safe to go out from Rameswaram today",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "browser_web_speech"
        assert body["tier"] == "client"
        assert body["lang"] == "en"

    def test_the_attempt_trail_names_every_provider_that_declined(
        self, client: TestClient
    ) -> None:
        """Audio has no visible provenance; the trail is how the UI can label it."""
        response = client.post(
            "/api/v1/speech/transcribe",
            json={"audio_base64": AUDIO, "browser_transcript": "wave height today"},
        )

        attempts = response.json()["attempts"]
        assert [a["provider"] for a in attempts] == [
            "bhashini_asr",
            "indicwhisper",
            "browser_web_speech",
        ]
        assert attempts[0]["outcome"] == "unavailable"

    def test_a_weak_transcript_is_flagged_for_confirmation(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/speech/transcribe",
            json={"audio_base64": AUDIO, "browser_transcript": "wave height today"},
        )

        assert response.json()["needs_confirmation"] is True

    def test_place_names_in_the_transcript_are_resolved(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/speech/transcribe",
            json={
                "audio_base64": AUDIO,
                "browser_transcript": "going from Rameswaram to Thoothukudi",
            },
        )

        places = response.json()["places"]
        assert [p["place_id"] for p in places] == ["in-tn-rameswaram", "in-tn-thoothukudi"]
        assert places[0]["lat"] == pytest.approx(9.29)

    def test_a_pinned_conversation_language_survives_a_weak_turn(
        self, client: TestClient
    ) -> None:
        """"ok" mid-conversation must not switch a Tamil session into English."""
        response = client.post(
            "/api/v1/speech/transcribe",
            json={"audio_base64": AUDIO, "browser_transcript": "ok", "pinned_language": "ta"},
        )

        body = response.json()
        assert body["reply_language"] == "ta"
        assert body["language_changed"] is False

    def test_no_recognizer_at_all_is_a_503_naming_the_reasons(
        self, client: TestClient
    ) -> None:
        """Not a 500: nothing is broken, everything is merely unreachable."""
        response = client.post("/api/v1/speech/transcribe", json={"audio_base64": AUDIO})

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert len(detail["attempts"]) == 3

    def test_invalid_base64_is_rejected_with_a_reason(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/speech/transcribe", json={"audio_base64": "not base64 at all!"}
        )

        assert response.status_code == 422

    def test_an_over_long_clip_is_refused_server_side(self, client: TestClient) -> None:
        """The 15 s cap is enforced here too; a client can be modified."""
        response = client.post(
            "/api/v1/speech/transcribe",
            json={
                "audio_base64": AUDIO,
                "duration_seconds": 30.0,
                "browser_transcript": "hello",
            },
        )

        assert response.status_code == 422
        assert "cap" in response.json()["detail"]


class TestSynthesize:
    def test_a_pre_generated_clip_is_served_with_no_provider_available(
        self, client: TestClient
    ) -> None:
        """The demo-day guarantee, through the HTTP surface (PLAN.md 7.7)."""
        text = scripted_texts(Language.TAMIL)[0]
        voice = select_voice(Language.TAMIL)
        key = AudioCacheKey.for_text(
            text, language=Language.TAMIL, voice=voice.voice_id, speed=1.0
        )
        speech_router._AUDIO_CACHE.put(key, b"pregenerated")

        # The endpoint normalizes, so it must be given the display text that yields it.
        response = client.post(
            "/api/v1/speech/synthesize", json={"text": text, "language": "ta"}
        )

        assert response.status_code == 200
        body = response.json()
        assert base64.b64decode(body["audio_base64"]) == b"pregenerated"
        assert body["cache_hit"] is True

    def test_the_spoken_text_is_returned_for_the_visible_transcript(
        self, client: TestClient
    ) -> None:
        """Phase 7.8 requires a visible transcript; it is only a check if it is returned."""
        text = "Wave height 2.5 m, wind 12 kn."
        spoken = normalize_for_speech(text, Language.TAMIL)
        voice = select_voice(Language.TAMIL)
        speech_router._AUDIO_CACHE.put(
            AudioCacheKey.for_text(
                spoken.text, language=Language.TAMIL, voice=voice.voice_id, speed=1.0
            ),
            b"clip",
        )

        response = client.post(
            "/api/v1/speech/synthesize", json={"text": text, "language": "ta"}
        )

        body = response.json()
        assert body["spoken_text"] == spoken.text
        assert "மீட்டர்" in body["spoken_text"]
        assert "2.5" in body["spoken_text"]

    def test_an_unprepared_sentence_with_nothing_configured_is_a_503(
        self, client: TestClient
    ) -> None:
        """Honest limit: the cache cannot speak a sentence nobody anticipated."""
        response = client.post(
            "/api/v1/speech/synthesize",
            json={"text": "a sentence nobody rehearsed", "language": "ta"},
        )

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert [a["provider"] for a in detail["attempts"]] == [
            "bhashini_tts",
            "indic_parler_tts",
            "pregenerated_cache",
        ]
        assert detail["spoken_text"]

    def test_text_with_unfilled_slots_is_refused(self, client: TestClient) -> None:
        """Otherwise the voice reads a brace aloud to a fisherman at sea."""
        response = client.post(
            "/api/v1/speech/synthesize",
            json={"text": "wave height {wave_height}", "language": "ta"},
        )

        assert response.status_code in {422, 503}
        if response.status_code == 422:
            assert "unfilled template slots" in response.json()["detail"]

    def test_an_unintelligible_speed_is_rejected_by_the_schema(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/speech/synthesize",
            json={"text": "hello", "language": "en", "speed": 3.0},
        )

        assert response.status_code == 422


class TestCapabilities:
    def test_it_reports_bhashini_as_not_configured(self, client: TestClient) -> None:
        """Reporting configured-ness rather than claiming capability."""
        body = client.get("/api/v1/speech/capabilities").json()

        assert body["bhashini_configured"] is False
        assert body["self_hosted_models_available"] is False

    def test_it_lists_ten_languages_and_marks_the_rehearsed_three(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/v1/speech/capabilities").json()

        assert len(body["languages"]) == 10
        rehearsed = {lang["code"] for lang in body["languages"] if lang["rehearsed"]}
        assert rehearsed == {"en", "ta", "hi"}

    def test_every_language_offers_at_least_one_voice(self, client: TestClient) -> None:
        body = client.get("/api/v1/speech/capabilities").json()

        assert all(lang["voices"] for lang in body["languages"])

    def test_the_documented_chains_are_reported_in_order(self, client: TestClient) -> None:
        body = client.get("/api/v1/speech/capabilities").json()

        assert body["asr_providers"] == ["bhashini_asr", "indicwhisper", "browser_web_speech"]
        assert body["tts_providers"] == [
            "bhashini_tts",
            "indic_parler_tts",
            "pregenerated_cache",
        ]


class TestCachedClipEndpoint:
    def test_a_cached_clip_can_be_fetched_by_key(self, client: TestClient) -> None:
        """An alert push carries the key; the device fetches without re-synthesizing."""
        voice = select_voice(Language.TAMIL)
        key = AudioCacheKey.for_text(
            "spoken", language=Language.TAMIL, voice=voice.voice_id, speed=1.0
        )
        speech_router._AUDIO_CACHE.put(key, b"alert-audio")

        response = client.get(f"/api/v1/speech/cache/{key.object_key}")

        assert response.status_code == 200
        assert base64.b64decode(response.json()["audio_base64"]) == b"alert-audio"

    def test_an_unknown_key_is_a_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/speech/cache/ta/ta_female_1/1_00/deadbeef.wav")

        assert response.status_code == 404
