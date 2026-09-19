"""Supported languages and script-based detection (PLAN.md Phase 7.3, 7.10).

**Tamil, Hindi and English are the rehearsed paths**; the other seven are enabled and
smoke-tested. That split is deliberate rather than apologetic — SAMUDRA already offers ten
languages through a menu, so breadth is not the differentiator. Auto-detection and
voice-first interaction are (gap G1), and those are worth proving properly in three
languages before claiming ten.

Detection is **script-based and deterministic**. Indic scripts occupy disjoint Unicode
blocks, so a Tamil utterance is identifiable by codepoint without a model, without a
network call, and identically every time. That matters more than it sounds: language
selection decides which TTS voice speaks a safety warning, and a non-deterministic
detector would make the same question answerable in different languages on different days.

Code-mixing ("Tanglish", "Hinglish") is the real complication — fisherfolk routinely mix
Tamil with English loanwords. It is detected by measuring the *share* of each script
rather than by finding a single foreign character, and the dominant script wins.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class Language(StrEnum):
    """Languages ORCA speaks. Values are BCP-47 codes."""

    ENGLISH = "en"
    TAMIL = "ta"
    HINDI = "hi"
    TELUGU = "te"
    MALAYALAM = "ml"
    ODIA = "or"
    BENGALI = "bn"
    GUJARATI = "gu"
    MARATHI = "mr"
    KANNADA = "kn"


# The three paths that must work flawlessly on stage (PLAN.md 7.10).
REHEARSED: frozenset[Language] = frozenset(
    {Language.ENGLISH, Language.TAMIL, Language.HINDI}
)


@dataclass(frozen=True)
class LanguageProfile:
    """What ORCA needs to know to read and speak a language."""

    language: Language
    english_name: str
    native_name: str
    unicode_block_prefix: str | None
    # Bhashini/ULCA uses ISO 639-1 for these; kept explicit so a mismatch is visible.
    ulca_code: str
    rehearsed: bool

    @property
    def is_latin(self) -> bool:
        return self.unicode_block_prefix is None


PROFILES: dict[Language, LanguageProfile] = {
    Language.ENGLISH: LanguageProfile(
        Language.ENGLISH, "English", "English", None, "en", True
    ),
    Language.TAMIL: LanguageProfile(
        Language.TAMIL, "Tamil", "தமிழ்", "TAMIL", "ta", True
    ),
    Language.HINDI: LanguageProfile(
        Language.HINDI, "Hindi", "हिन्दी", "DEVANAGARI", "hi", True
    ),
    Language.TELUGU: LanguageProfile(
        Language.TELUGU, "Telugu", "తెలుగు", "TELUGU", "te", False
    ),
    Language.MALAYALAM: LanguageProfile(
        Language.MALAYALAM, "Malayalam", "മലയാളം", "MALAYALAM", "ml", False
    ),
    Language.ODIA: LanguageProfile(Language.ODIA, "Odia", "ଓଡ଼ିଆ", "ORIYA", "or", False),
    Language.BENGALI: LanguageProfile(
        Language.BENGALI, "Bengali", "বাংলা", "BENGALI", "bn", False
    ),
    Language.GUJARATI: LanguageProfile(
        Language.GUJARATI, "Gujarati", "ગુજરાતી", "GUJARATI", "gu", False
    ),
    # Marathi shares Devanagari with Hindi, so script alone cannot separate them. The
    # detector reports Hindi for Devanagari and the user's stored preference resolves it;
    # guessing between them from script would be wrong half the time.
    Language.MARATHI: LanguageProfile(
        Language.MARATHI, "Marathi", "मराठी", "DEVANAGARI", "mr", False
    ),
    Language.KANNADA: LanguageProfile(
        Language.KANNADA, "Kannada", "ಕನ್ನಡ", "KANNADA", "kn", False
    ),
}

# Script prefix → the language reported for it. Devanagari maps to Hindi because it is
# the more common of the two Devanagari languages ORCA supports; Marathi speakers are
# resolved by their stored preference rather than by a coin flip.
SCRIPT_TO_LANGUAGE: dict[str, Language] = {
    "TAMIL": Language.TAMIL,
    "DEVANAGARI": Language.HINDI,
    "TELUGU": Language.TELUGU,
    "MALAYALAM": Language.MALAYALAM,
    "ORIYA": Language.ODIA,
    "BENGALI": Language.BENGALI,
    "GUJARATI": Language.GUJARATI,
    "KANNADA": Language.KANNADA,
}


def profile_for(language: Language) -> LanguageProfile:
    return PROFILES[language]


def script_of(character: str) -> str | None:
    """The Unicode script block a character belongs to, or None for Latin/punctuation."""
    if not character.isalpha():
        return None
    try:
        name = unicodedata.name(character)
    except ValueError:
        return None
    for prefix in SCRIPT_TO_LANGUAGE:
        if name.startswith(prefix):
            return prefix
    return None


def script_shares(text: str) -> dict[str, float]:
    """Fraction of alphabetic characters belonging to each script.

    Latin is reported under ``"LATIN"`` so code-mixing is measurable rather than merely
    detectable.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return {}

    counts: dict[str, int] = {}
    for character in letters:
        script = script_of(character) or "LATIN"
        counts[script] = counts.get(script, 0) + 1
    total = len(letters)
    return {script: count / total for script, count in counts.items()}
