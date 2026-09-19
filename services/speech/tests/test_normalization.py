"""Numeric and unit preservation through the speech path (PLAN.md Phase 7.5, 7.6).

This is the most safety-relevant test file in the speech service. The number a fisherman
hears is the number a kernel computed; if anything between the two can change it, the
"kernels own every number" guarantee from Phase 4 stops being true at the last hop, where
it is least visible — audio has no transcript to check against unless one is shown.

So the assertions here are about digits, not about phrasing.
"""

from __future__ import annotations

import pytest

from orca_speech import (
    Language,
    NumericDriftError,
    extract_numbers,
    normalize_for_speech,
    spoken_unit,
)
from orca_speech.normalize import UNIT_TABLE


class TestNumbersSurviveNormalization:
    @pytest.mark.parametrize("language", [Language.ENGLISH, Language.TAMIL, Language.HINDI])
    @pytest.mark.parametrize(
        "text",
        [
            "Wave height 2.5 m, wind 12 kn.",
            "Safety score 78 out of 100.",
            "SST 28.5 degC, chlorophyll 0.35 %.",
            "Distance 18.4 km, travel time 1.4 h.",
            "Drift -0.4 m/s over 30 min.",
            "Pressure 1004 hPa, bearing 110 deg.",
        ],
    )
    def test_every_digit_is_unchanged(self, text: str, language: Language) -> None:
        result = normalize_for_speech(text, language)
        assert result.numbers == extract_numbers(text)
        assert extract_numbers(result.text) == extract_numbers(text)

    def test_a_range_is_spoken_as_a_range_not_a_subtraction(self) -> None:
        """"1.5-2.0 m" read as "1.5 minus 2.0" would be heard as a single wrong number."""
        result = normalize_for_speech("Waves 1.5-2.0 m today.", Language.ENGLISH)

        assert "to" in result.text
        assert result.numbers == ("1.5", "2.0")

    def test_a_genuine_negative_keeps_its_sign(self) -> None:
        """Current set against the heading is negative; dropping the sign reverses it."""
        result = normalize_for_speech("Along-track current -0.4 m/s.", Language.ENGLISH)

        assert "-0.4" in result.text

    def test_a_unit_attached_without_a_space_is_still_expanded(self) -> None:
        """Upstream bulletins and ASR output both write "2.5m"."""
        result = normalize_for_speech("Waves 2.5m and wind 12kn.", Language.ENGLISH)

        assert "metres" in result.text
        assert "knots" in result.text
        assert result.numbers == ("2.5", "12")

    def test_decimal_word_expansion_still_preserves_the_value(self) -> None:
        """Voices that need "2 point 5" must not thereby be able to lose the 5."""
        result = normalize_for_speech("2.5 m", Language.TAMIL, decimal_as_word=True)

        assert "புள்ளி" in result.text
        assert result.numbers == ("2.5",)

    def test_the_guard_catches_a_broken_expansion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A rewrite that rounded a value must fail loudly rather than be spoken.

        The guard is the reason this module can be trusted at all, so it is tested by
        breaking the normalizer on purpose and checking that it refuses.
        """
        from orca_speech import normalize as module

        monkeypatch.setattr(module, "_expand_decimals", lambda text, language: "rounded to 3 m")
        with pytest.raises(NumericDriftError):
            module.normalize_for_speech("2.5 m", Language.ENGLISH, decimal_as_word=True)


class TestUnitExpansion:
    @pytest.mark.parametrize(
        ("unit", "language", "expected"),
        [
            ("m", Language.ENGLISH, "metres"),
            ("m", Language.TAMIL, "மீட்டர்"),
            ("m", Language.HINDI, "मीटर"),
            ("kn", Language.ENGLISH, "knots"),
            ("degC", Language.ENGLISH, "degrees Celsius"),
            ("%", Language.HINDI, "प्रतिशत"),
        ],
    )
    def test_symbols_become_words(self, unit: str, language: Language, expected: str) -> None:
        assert spoken_unit(unit, language) == expected

    def test_singular_and_plural_differ_where_the_language_does(self) -> None:
        assert spoken_unit("m", Language.ENGLISH, 1.0) == "metre"
        assert spoken_unit("m", Language.ENGLISH, 2.5) == "metres"

    def test_an_unmapped_unit_falls_through_untouched(self) -> None:
        """Better a symbol in the transcript than an invented word in the audio."""
        assert spoken_unit("furlong", Language.ENGLISH) is None
        result = normalize_for_speech("Depth 40 furlong", Language.ENGLISH)
        assert "furlong" in result.text

    def test_the_degree_symbol_is_separated_from_its_number(self) -> None:
        result = normalize_for_speech("SST 28.5°C", Language.ENGLISH)

        assert result.text == "SST 28.5 degrees Celsius"

    @pytest.mark.parametrize("unit", sorted(UNIT_TABLE))
    def test_every_unit_is_spoken_in_all_three_rehearsed_languages(self, unit: str) -> None:
        """A missing entry would leave a bare symbol in Tamil or Hindi audio."""
        for language in (Language.ENGLISH, Language.TAMIL, Language.HINDI):
            assert spoken_unit(unit, language), f"{unit} has no {language.value} form"

    def test_a_hindi_sentence_ending_in_a_danda_still_expands_its_unit(self) -> None:
        """"12 kn।" would otherwise arrive as the unrecognised token "kn।"."""
        result = normalize_for_speech("हवा 12 kn।", Language.HINDI)

        assert "नॉट।" in result.text
