"""Number and unit normalization before synthesis (PLAN.md Phase 7.6, §1.3).

Indic TTS engines mangle unit symbols. "Hs 2.5 m" is read as a letter, a number and a
stray consonant; "12 kn" becomes nonsense; "28°C" is anyone's guess. So the rendered
answer is rewritten into spoken form **before** it reaches a synthesizer — by table, not
by a model.

The rule this module exists to enforce:

    **The digits are never touched.**

Units expand, symbols expand, spacing is fixed — but the ordered sequence of numeric
tokens in the output is byte-identical to the input. That is checked, not intended:
:func:`normalize_for_speech` extracts the numbers before and after and raises
:class:`NumericDriftError` if they differ. A normalizer that silently rounded 2.5 m to
"two and a half metres" would be a safety defect, because the number a fisherman hears is
the number the kernel computed and nothing else is allowed to reinterpret it.

Why a table and not an LLM, stated plainly: an LLM asked to "read this naturally" will
occasionally round, convert units, or translate a figure into words that lose precision,
and it will do so non-reproducibly. There is no version of that which is acceptable on the
audio path of a life-safety advisory (PLAN.md 7.5, 7.6).

Decimal handling is deliberately conservative. The default keeps "2.5" as digits, because
every Bhashini and Parler voice tested in the fallback chain reads a decimal digit string
correctly, and expanding it into words is exactly where precision gets lost. Word
expansion is available per language (``decimal_as_word=True``) for voices that need it,
and the same digit-preservation check still applies to the digits either side.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from orca_speech.languages import Language

# A number as it appears in rendered kernel output: optional sign, digits, optional
# fractional part. Thousands separators are not produced by ORCA's renderers, so they are
# not matched here — a comma stays a list separator.
#
# The lookbehind stops a hyphen being read as a minus sign when it separates two numbers:
# "1.5-2.0 m" is a range, and parsing it as 1.5 and −2.0 would make the range expansion
# below look like it had altered a value. Ranges are common in wave-height bulletins, so
# this is the ordinary case rather than an edge one.
NUMBER_PATTERN = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?")


class NumericDriftError(ValueError):
    """Raised when normalization changed a number. Always a bug, never a warning.

    Caught nowhere by design: if the audio path can alter a wave height, the whole
    "kernels own every number" guarantee (Phase 4) stops being true at the last hop.
    """

    def __init__(self, before: tuple[str, ...], after: tuple[str, ...], text: str) -> None:
        super().__init__(
            f"normalization altered numeric content: {before} -> {after} in {text!r}"
        )
        self.before = before
        self.after = after


@dataclass(frozen=True)
class SpokenUnit:
    """How a unit symbol is spoken, per language.

    ``singular``/``plural`` differ only where the language distinguishes them in speech;
    Indic entries repeat the same form, which is correct rather than lazy — Tamil
    "மீட்டர்" does not inflect for a spoken measurement.
    """

    singular: str
    plural: str

    def spoken(self, value: float | None) -> str:
        if value is None:
            return self.plural
        return self.singular if abs(value) == 1 else self.plural


# Unit symbol → spoken form, per language.
#
# Keys are the canonical symbols the kernels emit (ObservationRecord canonical units),
# so a new unit in a kernel surfaces here as a missing key rather than as garbled audio.
UNIT_TABLE: Mapping[str, Mapping[Language, SpokenUnit]] = {
    "m": {
        Language.ENGLISH: SpokenUnit("metre", "metres"),
        Language.TAMIL: SpokenUnit("மீட்டர்", "மீட்டர்"),
        Language.HINDI: SpokenUnit("मीटर", "मीटर"),
    },
    "km": {
        Language.ENGLISH: SpokenUnit("kilometre", "kilometres"),
        Language.TAMIL: SpokenUnit("கிலோமீட்டர்", "கிலோமீட்டர்"),
        Language.HINDI: SpokenUnit("किलोमीटर", "किलोमीटर"),
    },
    "m/s": {
        Language.ENGLISH: SpokenUnit("metre per second", "metres per second"),
        Language.TAMIL: SpokenUnit("மீட்டர் பெர் செகண்ட்", "மீட்டர் பெர் செகண்ட்"),
        Language.HINDI: SpokenUnit("मीटर प्रति सेकंड", "मीटर प्रति सेकंड"),
    },
    "km/h": {
        Language.ENGLISH: SpokenUnit("kilometre per hour", "kilometres per hour"),
        Language.TAMIL: SpokenUnit("மணிக்கு கிலோமீட்டர்", "மணிக்கு கிலோமீட்டர்"),
        Language.HINDI: SpokenUnit("किलोमीटर प्रति घंटा", "किलोमीटर प्रति घंटा"),
    },
    "kn": {
        Language.ENGLISH: SpokenUnit("knot", "knots"),
        Language.TAMIL: SpokenUnit("நாட்", "நாட்"),
        Language.HINDI: SpokenUnit("नॉट", "नॉट"),
    },
    "s": {
        Language.ENGLISH: SpokenUnit("second", "seconds"),
        Language.TAMIL: SpokenUnit("செகண்ட்", "செகண்ட்"),
        Language.HINDI: SpokenUnit("सेकंड", "सेकंड"),
    },
    "h": {
        Language.ENGLISH: SpokenUnit("hour", "hours"),
        Language.TAMIL: SpokenUnit("மணி நேரம்", "மணி நேரம்"),
        Language.HINDI: SpokenUnit("घंटा", "घंटे"),
    },
    "min": {
        Language.ENGLISH: SpokenUnit("minute", "minutes"),
        Language.TAMIL: SpokenUnit("நிமிடம்", "நிமிடம்"),
        Language.HINDI: SpokenUnit("मिनट", "मिनट"),
    },
    "degC": {
        Language.ENGLISH: SpokenUnit("degree Celsius", "degrees Celsius"),
        Language.TAMIL: SpokenUnit("டிகிரி செல்சியஸ்", "டிகிரி செல்சியஸ்"),
        Language.HINDI: SpokenUnit("डिग्री सेल्सियस", "डिग्री सेल्सियस"),
    },
    "deg": {
        Language.ENGLISH: SpokenUnit("degree", "degrees"),
        Language.TAMIL: SpokenUnit("டிகிரி", "டிகிரி"),
        Language.HINDI: SpokenUnit("डिग्री", "डिग्री"),
    },
    "%": {
        Language.ENGLISH: SpokenUnit("percent", "percent"),
        Language.TAMIL: SpokenUnit("சதவீதம்", "சதவீதம்"),
        Language.HINDI: SpokenUnit("प्रतिशत", "प्रतिशत"),
    },
    "hPa": {
        Language.ENGLISH: SpokenUnit("hectopascal", "hectopascals"),
        Language.TAMIL: SpokenUnit("ஹெக்டோபாஸ்கல்", "ஹெக்டோபாஸ்கல்"),
        Language.HINDI: SpokenUnit("हेक्टोपास्कल", "हेक्टोपास्कल"),
    },
    "nmi": {
        Language.ENGLISH: SpokenUnit("nautical mile", "nautical miles"),
        Language.TAMIL: SpokenUnit("கடல் மைல்", "கடல் மைல்"),
        Language.HINDI: SpokenUnit("समुद्री मील", "समुद्री मील"),
    },
}

# Symbols that appear attached to a number without a space and must be separated before
# the unit table can see them.
SYMBOL_ALIASES: Mapping[str, str] = {
    "°C": "degC",
    "℃": "degC",
    "°": "deg",
}

# Spoken decimal separator, used only when ``decimal_as_word`` is requested.
DECIMAL_WORD: Mapping[Language, str] = {
    Language.ENGLISH: "point",
    Language.TAMIL: "புள்ளி",
    Language.HINDI: "दशमलव",
}

# Range separator: "1.5-2.0 m" should be spoken as a range, not a subtraction.
RANGE_WORD: Mapping[Language, str] = {
    Language.ENGLISH: "to",
    Language.TAMIL: "முதல்",
    Language.HINDI: "से",
}

_RANGE_PATTERN = re.compile(r"(?<=\d)\s*[-–—]\s*(?=\d)")

# Clause punctuation that can follow a unit. The Devanagari danda is included because
# Hindi templates end sentences with it, and "{wind_speed}।" would otherwise arrive as the
# unrecognised token "m/s।" and be read out as a symbol.
TRAILING_PUNCTUATION = ".,;:!?।॥"

# "2.5m" written without a space — common in ASR output and in alert text copied from
# upstream bulletins. ORCA's own formatters always insert the space, so this only repairs
# input from elsewhere. Longest symbols first so "km" is not matched as "k" + "m".
_UNIT_SYMBOLS_ALTERNATION = "|".join(
    re.escape(symbol) for symbol in sorted(UNIT_TABLE, key=len, reverse=True)
)
_UNIT_ATTACH_PATTERN = re.compile(
    rf"(?<=\d)(?=(?:{_UNIT_SYMBOLS_ALTERNATION})(?:\b|$))"
)


@dataclass(frozen=True)
class NormalizedSpeech:
    """Text ready for synthesis, plus what was done to it.

    ``numbers`` is the evidence for the central guarantee: these digit strings appear, in
    this order, in both the input and the output.
    """

    text: str
    language: Language
    numbers: tuple[str, ...]
    expanded_units: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.expanded_units)


def extract_numbers(text: str) -> tuple[str, ...]:
    """Every numeric token, in order of appearance, exactly as written."""
    return tuple(NUMBER_PATTERN.findall(text))


def spoken_unit(unit: str, language: Language, value: float | None = None) -> str | None:
    """The spoken form of a unit symbol, or ``None`` if ORCA has no entry for it.

    Returning ``None`` rather than guessing is the point: an unmapped unit falls through
    unchanged and is visible in the transcript, instead of being invented in audio.
    """
    entry = UNIT_TABLE.get(unit)
    if entry is None:
        return None
    form = entry.get(language)
    if form is None:
        return None
    return form.spoken(value)


def normalize_for_speech(
    text: str,
    language: Language,
    *,
    decimal_as_word: bool = False,
) -> NormalizedSpeech:
    """Rewrite ``text`` into spoken form for ``language``, preserving every number.

    Raises :class:`NumericDriftError` if the numeric content changed — the check is the
    feature, so it runs on every call rather than only in tests.
    """
    before = extract_numbers(text)

    working = text
    for symbol, canonical in SYMBOL_ALIASES.items():
        working = working.replace(symbol, f" {canonical}")

    range_word = RANGE_WORD.get(language, RANGE_WORD[Language.ENGLISH])
    working = _RANGE_PATTERN.sub(f" {range_word} ", working)
    working = _UNIT_ATTACH_PATTERN.sub(" ", working)

    expanded: list[str] = []
    out_tokens: list[str] = []
    raw_tokens = working.split()
    for index, token in enumerate(raw_tokens):
        stripped, trailing = _split_trailing_punctuation(token)
        replacement = spoken_unit(stripped, language, _preceding_value(raw_tokens, index))
        if replacement is None:
            out_tokens.append(token)
            continue
        expanded.append(stripped)
        out_tokens.append(replacement + trailing)

    result = " ".join(out_tokens)
    if decimal_as_word:
        result = _expand_decimals(result, language)
        after = tuple(_collapse_decimal_words(result, language))
    else:
        after = extract_numbers(result)

    if after != before:
        raise NumericDriftError(before, after, text)

    return NormalizedSpeech(
        text=result,
        language=language,
        numbers=before,
        expanded_units=tuple(expanded),
    )


def _split_trailing_punctuation(token: str) -> tuple[str, str]:
    """Separate a unit from clause punctuation: ``"m,"`` -> ``("m", ",")``."""
    index = len(token)
    while index > 0 and token[index - 1] in TRAILING_PUNCTUATION:
        index -= 1
    # A bare "." would strip the decimal of a number; units never end in a digit, so a
    # token that is entirely punctuation is left alone.
    if index == 0:
        return token, ""
    return token[:index], token[index:]


def _preceding_value(raw_tokens: list[str], index: int) -> float | None:
    """The number immediately before a unit token, for singular/plural selection."""
    if index == 0:
        return None
    match = NUMBER_PATTERN.fullmatch(raw_tokens[index - 1])
    return float(match.group()) if match else None


def _expand_decimals(text: str, language: Language) -> str:
    word = DECIMAL_WORD.get(language, DECIMAL_WORD[Language.ENGLISH])

    def replace(match: re.Match[str]) -> str:
        token = match.group()
        if "." not in token:
            return token
        whole, fraction = token.split(".", 1)
        return f"{whole} {word} {fraction}"

    return NUMBER_PATTERN.sub(replace, text)


def _collapse_decimal_words(text: str, language: Language) -> list[str]:
    """Re-read expanded decimals as their original tokens, to verify nothing was lost."""
    word = DECIMAL_WORD.get(language, DECIMAL_WORD[Language.ENGLISH])
    collapsed = re.sub(rf"(-?\d+)\s+{re.escape(word)}\s+(\d+)", r"\1.\2", text)
    return list(NUMBER_PATTERN.findall(collapsed))
