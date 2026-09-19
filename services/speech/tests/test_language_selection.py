"""Language detection and conversation pinning (PLAN.md Phase 7.3, 7.10).

Language selection decides which voice reads a safety warning aloud, so the properties
worth asserting are stability and restraint: the same input always yields the same
language, and an ambiguous input defers to the user rather than guessing.
"""

from __future__ import annotations

import pytest

from orca_speech import (
    REHEARSED,
    DetectionMethod,
    Language,
    LexiconArbiter,
    detect_language,
    profile_for,
    resolve_conversation_language,
    script_shares,
)


class TestScriptDetection:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("இன்று கடலுக்கு போகலாமா?", Language.TAMIL),
            ("आज समुद्र जाना सुरक्षित है?", Language.HINDI),
            ("Is it safe to go out today?", Language.ENGLISH),
            ("ఈరోజు సముద్రం ఎలా ఉంది?", Language.TELUGU),
            ("ഇന്ന് കടലിൽ പോകാമോ?", Language.MALAYALAM),
            ("আজ সমুদ্রে যাওয়া নিরাপদ?", Language.BENGALI),
        ],
    )
    def test_script_identifies_the_language(self, text: str, expected: Language) -> None:
        assert detect_language(text).language is expected

    def test_script_detection_is_reported_as_certain(self) -> None:
        detection = detect_language("இன்று கடல் எப்படி இருக்கும்?")

        assert detection.method is DetectionMethod.SCRIPT
        assert detection.is_certain

    def test_detection_is_stable_across_repeated_calls(self) -> None:
        """A detector that drifted would answer the same question in different languages."""
        results = {detect_language("இன்று கடல் எப்படி?").language for _ in range(25)}

        assert results == {Language.TAMIL}

    def test_an_english_loanword_does_not_change_the_language(self) -> None:
        """Fisherfolk say "GPS" and "diesel" in Tamil sentences all day."""
        detection = detect_language("இன்று கடல் GPS படி எப்படி இருக்கும்?")

        assert detection.language is Language.TAMIL

    def test_heavy_mixing_is_reported_as_mixed(self) -> None:
        detection = detect_language("கடல் conditions today safe ah illa unsafe ah?")

        assert detection.code_mixed
        assert detection.secondary is Language.ENGLISH


class TestDevanagariAmbiguity:
    def test_devanagari_defaults_to_hindi(self) -> None:
        assert detect_language("समुद्र में जाना सुरक्षित है?").language is Language.HINDI

    def test_a_marathi_preference_resolves_the_shared_script(self) -> None:
        """Script cannot separate Hindi from Marathi; the user's own setting can."""
        detection = detect_language(
            "समुद्रात जाणे सुरक्षित आहे का?", preference=Language.MARATHI
        )

        assert detection.language is Language.MARATHI


class TestRomanizedCodeMix:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("innaikku kadal safe ah irukka?", Language.TAMIL),
            ("naalaikku poga mudiyuma?", Language.TAMIL),
            ("aaj samundar kaisa hai bhai?", Language.HINDI),
            ("kal machli pakadne jaana chahiye kya?", Language.HINDI),
            ("what is the wave height today?", Language.ENGLISH),
        ],
    )
    def test_romanized_indic_is_separated_from_english(
        self, text: str, expected: Language
    ) -> None:
        assert detect_language(text).language is expected

    def test_a_short_ambiguous_utterance_defers_to_the_preference(self) -> None:
        """"ok" carries no signal; guessing would be worse than using what the user chose."""
        detection = detect_language("ok", preference=Language.TAMIL)

        assert detection.language is Language.TAMIL
        assert detection.method is DetectionMethod.PREFERENCE

    def test_with_no_preference_an_ambiguous_utterance_falls_to_english(self) -> None:
        detection = detect_language("ok")

        assert detection.language is Language.ENGLISH
        assert detection.method is DetectionMethod.DEFAULT

    def test_the_lexicon_arbiter_declines_a_genuine_tie(self) -> None:
        """Returning a coin-flip answer would be worse than admitting the tie."""
        arbiter = LexiconArbiter()

        assert arbiter.arbitrate("kadal samundar", [Language.TAMIL, Language.HINDI]) is None

    def test_the_lexicon_arbiter_picks_a_clear_winner(self) -> None:
        arbiter = LexiconArbiter()

        chosen = arbiter.arbitrate(
            "innaikku kadal alai eppadi irukku", [Language.TAMIL, Language.HINDI]
        )

        assert chosen is Language.TAMIL


class TestConversationPinning:
    def test_the_first_turn_pins_the_language(self) -> None:
        detection = detect_language("இன்று கடல் எப்படி?")

        language, changed = resolve_conversation_language(detection, pinned=None)

        assert language is Language.TAMIL
        assert changed

    def test_a_weak_signal_does_not_unpin_a_conversation(self) -> None:
        """Answering "ok" mid-conversation must not switch everything to English."""
        detection = detect_language("ok")

        language, changed = resolve_conversation_language(detection, pinned=Language.TAMIL)

        assert language is Language.TAMIL
        assert not changed

    def test_a_confident_script_switch_does_repin(self) -> None:
        """If they genuinely switch to Hindi, ORCA should follow."""
        detection = detect_language("आज समुद्र में जाना सुरक्षित है क्या?")

        language, changed = resolve_conversation_language(detection, pinned=Language.TAMIL)

        assert language is Language.HINDI
        assert changed

    def test_a_romanized_guess_does_not_repin(self) -> None:
        """The lexicon is not confident enough to override a pinned language."""
        detection = detect_language("aaj samundar kaisa hai")

        language, changed = resolve_conversation_language(detection, pinned=Language.TAMIL)

        assert language is Language.TAMIL
        assert not changed


class TestLanguageCoverage:
    def test_the_rehearsed_three_are_the_ones_the_plan_names(self) -> None:
        assert {Language.ENGLISH, Language.TAMIL, Language.HINDI} == REHEARSED

    def test_ten_languages_are_declared(self) -> None:
        assert len(list(Language)) == 10

    def test_every_language_has_a_profile_with_a_ulca_code(self) -> None:
        for language in Language:
            profile = profile_for(language)
            assert profile.ulca_code
            assert profile.native_name

    def test_script_shares_sum_to_one(self) -> None:
        shares = script_shares("கடல் conditions")

        assert pytest.approx(sum(shares.values()), abs=1e-9) == 1.0
