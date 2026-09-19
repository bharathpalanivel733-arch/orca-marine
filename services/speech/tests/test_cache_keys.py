"""Audio cache keys (PLAN.md Phase 7.7).

The key is what makes the outage protection real. If it varied with anything other than
the audio content, the pre-generated cache would never be hit and "the demo works with
Bhashini down" would be a claim with nothing behind it.

So the tests assert both directions: same audio means same key, and every input that
changes the audio changes the key.
"""

from __future__ import annotations

import hashlib

import pytest

from orca_speech import (
    AudioCacheKey,
    InMemoryAudioCache,
    Language,
    has_unfilled_slots,
    key_set,
)


def key(text: str = "wave height 2.5 metres", **overrides: object) -> AudioCacheKey:
    params: dict[str, object] = {
        "language": Language.TAMIL,
        "voice": "ta_female_1",
        "speed": 1.0,
    }
    params.update(overrides)
    return AudioCacheKey.for_text(text, **params)  # type: ignore[arg-type]


class TestKeyComposition:
    def test_the_key_is_the_sha256_of_the_text(self) -> None:
        text = "wave height 2.5 metres"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()

        assert key(text).text_sha256 == expected

    def test_the_same_inputs_give_the_same_key(self) -> None:
        """Without this the pre-generation job and the live path address different objects."""
        assert key().object_key == key().object_key

    @pytest.mark.parametrize(
        "overrides",
        [
            {"language": Language.HINDI},
            {"voice": "ta_male_1"},
            {"speed": 1.2},
            {"audio_format": "mp3"},
        ],
        ids=["language", "voice", "speed", "format"],
    )
    def test_anything_that_changes_the_audio_changes_the_key(
        self, overrides: dict[str, object]
    ) -> None:
        assert key(**overrides).object_key != key().object_key

    def test_different_text_gives_a_different_key(self) -> None:
        """A changed wave height must not be able to serve yesterday's clip."""
        assert key("wave height 2.5 metres").object_key != key("wave height 3.1 metres").object_key

    def test_the_object_key_is_language_first(self) -> None:
        """A language's whole cache is listed, counted and purged by prefix."""
        assert key().object_key.startswith("ta/ta_female_1/")

    def test_the_object_key_ends_with_the_format(self) -> None:
        assert key().object_key.endswith(".wav")
        assert key(audio_format="mp3").object_key.endswith(".mp3")

    def test_speed_is_quantized_so_float_noise_does_not_fragment_the_cache(self) -> None:
        """A slider emitting 1.0000001 would otherwise miss every pre-generated clip."""
        assert key(speed=1.0).object_key == key(speed=1.0000001).object_key

    def test_speed_differences_that_matter_are_still_distinguished(self) -> None:
        assert key(speed=1.0).object_key != key(speed=1.05).object_key

    def test_unicode_text_hashes_stably(self) -> None:
        """Tamil text must key identically on every platform, not per filesystem encoding."""
        tamil = "அலை உயரம் 2.5 மீட்டர்"

        assert key(tamil).text_sha256 == hashlib.sha256(tamil.encode("utf-8")).hexdigest()


class TestCacheBehaviour:
    def test_a_stored_clip_is_returned_for_its_key(self) -> None:
        cache = InMemoryAudioCache()
        cache.put(key(), b"RIFF-audio")

        found = cache.get(key())

        assert found is not None
        assert found.audio == b"RIFF-audio"
        assert found.size_bytes == 10

    def test_a_missing_clip_returns_none_rather_than_raising(self) -> None:
        """A cache miss is an ordinary event on the way to a live provider."""
        assert InMemoryAudioCache().get(key()) is None

    def test_has_matches_get(self) -> None:
        cache = InMemoryAudioCache()

        assert not cache.has(key())
        cache.put(key(), b"audio")
        assert cache.has(key())

    def test_a_clip_stored_under_one_voice_is_not_served_for_another(self) -> None:
        cache = InMemoryAudioCache()
        cache.put(key(voice="ta_female_1"), b"female-audio")

        assert cache.get(key(voice="ta_male_1")) is None


class TestBatchKeys:
    def test_repeated_text_is_synthesized_once(self) -> None:
        """The demo script says the same abstention in several places."""
        keys = key_set(
            ["a sentence", "another sentence", "a sentence"],
            language=Language.TAMIL,
            voice="ta_female_1",
        )

        assert len(keys) == 2

    def test_batch_keys_match_individually_computed_keys(self) -> None:
        keys = key_set(["one line"], language=Language.TAMIL, voice="ta_female_1")

        assert keys[0].object_key == key("one line").object_key


class TestSlotGuard:
    def test_unfilled_slots_are_detected(self) -> None:
        """Caching this would produce audio reading a brace aloud, served confidently."""
        assert has_unfilled_slots("wave height {wave_height}")

    def test_filled_text_passes(self) -> None:
        assert not has_unfilled_slots("wave height 2.5 metres")

    def test_ordinary_braces_in_prose_are_not_mistaken_for_slots(self) -> None:
        assert not has_unfilled_slots("the set {A, B}")
