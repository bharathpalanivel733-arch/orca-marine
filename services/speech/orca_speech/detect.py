"""Language and code-mix detection (PLAN.md Phase 7.3).

Detection is deterministic. Given the same transcript it returns the same language every
time, without a network call and without a model. That property is not a nicety: the
detected language chooses the voice that reads a safety warning aloud, and a detector that
drifted would make the same question answerable in different languages on different days.

Three signals, tried in a fixed order, each recording *how* it decided:

1. **Script.** Indic scripts occupy disjoint Unicode blocks, so Tamil or Devanagari text
   identifies itself by codepoint. This is exact and is tried first.
2. **Romanized markers.** "Tanglish" and "Hinglish" — Tamil or Hindi written in Latin
   letters — carry no script signal at all. A small lexicon of high-frequency function
   words ("enna", "kadal", "kaise", "samundar") separates them from English. Function
   words are used rather than content words because they are what actually differs;
   "wave" and "boat" are borrowed into both.
3. **Stored preference.** When the text is too short or too ambiguous to call, the user's
   own setting decides. Guessing would be worse than deferring to what they chose.

**Code-mixing is measured, not merely flagged.** A Tamil sentence with two English
loanwords should still be answered in Tamil; an English sentence with one Tamil word
should not become a Tamil answer. Mixing is therefore reported as a share, and the
dominant script wins.

A seam for a model-based arbiter exists (:class:`CodeMixArbiter`) because romanized
code-mix is genuinely hard. **No model-backed arbiter is wired in**, and the default
:class:`LexiconArbiter` is deterministic. This is recorded as an open gap rather than
described as done.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from orca_speech.languages import SCRIPT_TO_LANGUAGE, Language, script_shares

# Below this share of a single non-Latin script, the text is treated as Latin-dominant and
# the romanized lexicon decides. Chosen so one or two Indic words inside an otherwise
# English sentence do not flip the answer language.
DOMINANT_SCRIPT_SHARE = 0.5

# Above this share of a *second* script, the utterance is reported as code-mixed. A
# single loanword (well under a fifth of a sentence) is normal speech, not code-mixing.
CODE_MIX_SHARE = 0.2

# Shorter than this, romanized text is not worth guessing at: "ok", "yes", "seri" carry
# almost no signal, and the stored preference is a better answer than a coin flip.
MIN_ROMANIZED_LETTERS = 8


class DetectionMethod(StrEnum):
    """How a language was decided. Carried into provenance so a wrong answer is diagnosable."""

    SCRIPT = "script"
    ROMANIZED_LEXICON = "romanized_lexicon"
    PREFERENCE = "preference"
    PROVIDER = "provider"
    ARBITER = "arbiter"
    DEFAULT = "default"


# High-frequency romanized function words and marine vocabulary, by language.
#
# Function words carry the signal: "enna", "eppo", "kaise", "kitna" have no English
# reading, whereas "boat", "wind" and "safe" are used in all three and would only add
# noise. Marine nouns are included where the romanization is unambiguous.
ROMANIZED_MARKERS: Mapping[Language, frozenset[str]] = {
    Language.TAMIL: frozenset(
        {
            "enna", "eppo", "epdi", "eppadi", "yaaru", "enga", "engha", "illa", "illai",
            "irukku", "irukka", "iruku", "vanthu", "ponum", "poga", "polam", "mudiyuma",
            "seri", "sari", "nalla", "romba", "konjam", "innaikku", "naalaikku", "indha",
            "andha", "kadal", "kadalu", "meenu", "padagu", "vallam", "thuraimugam",
            "alai", "kaathu", "mazhai", "apuram", "aprom", "veetuku",
        }
    ),
    Language.HINDI: frozenset(
        {
            "kya", "kaise", "kaisa", "kitna", "kitni", "kahan", "kab", "kyun", "kyon",
            "hain", "nahi", "nahin", "mera", "meri", "humko", "mujhe",
            "aaj", "abhi", "thoda", "bahut", "accha", "achha", "theek", "thik",
            "samundar", "samandar", "machli", "machhli", "naav", "hawa", "lehar",
            "barish", "toofan", "tufan", "bandargah", "jaana", "chahiye",
        }
    ),
}

# Words that look like markers but are ordinary English. Without this, a stray "kale" or
# "hail" would drag an English sentence into another language.
ENGLISH_OVERRIDES: frozenset[str] = frozenset({"hair", "hail", "hawaii", "kale", "meen"})


@dataclass(frozen=True)
class LanguageDetection:
    """What was detected, how, and how sure.

    ``confidence`` is a reported property of the *method*, not a learned probability:
    script detection is exact, lexicon detection is not. It is never used to scale a
    numeric answer — only to decide whether to ask the user to confirm.
    """

    language: Language
    method: DetectionMethod
    confidence: float
    code_mixed: bool = False
    secondary: Language | None = None
    shares: Mapping[str, float] = field(default_factory=dict)
    matched_markers: tuple[str, ...] = ()

    @property
    def is_certain(self) -> bool:
        return self.method is DetectionMethod.SCRIPT and not self.code_mixed


@runtime_checkable
class CodeMixArbiter(Protocol):
    """Seam for a model-based tie-break on romanized code-mix.

    Deliberately narrow: an arbiter sees only the transcript and the candidate languages,
    and returns one of the candidates or ``None``. It cannot translate, cannot rewrite the
    transcript, and never touches a number — the only thing it may influence is which
    pre-translated template catalogue is used.
    """

    def arbitrate(self, text: str, candidates: Sequence[Language]) -> Language | None: ...


class LexiconArbiter:
    """Default arbiter: pick the candidate with more romanized marker hits.

    Deterministic, offline, and wrong sometimes — but wrong in a way that is inspectable
    and identical on every run. A tie is reported as "cannot tell" rather than resolved
    arbitrarily.
    """

    def arbitrate(self, text: str, candidates: Sequence[Language]) -> Language | None:
        counts = {lang: len(markers_in(text, lang)) for lang in candidates}
        if not counts:
            return None
        best = max(counts, key=lambda lang: counts[lang])
        if counts[best] == 0:
            return None
        if sum(1 for value in counts.values() if value == counts[best]) > 1:
            return None
        return best


def tokens(text: str) -> list[str]:
    """Lowercased alphabetic tokens. Punctuation and digits are separators."""
    cleaned = "".join(c.lower() if c.isalpha() or c.isspace() else " " for c in text)
    return [t for t in cleaned.split() if t]


def markers_in(text: str, language: Language) -> tuple[str, ...]:
    """Distinct romanized markers for ``language`` present in ``text``."""
    markers = ROMANIZED_MARKERS.get(language, frozenset())
    hits = [t for t in tokens(text) if t in markers and t not in ENGLISH_OVERRIDES]
    return tuple(sorted(set(hits)))


def detect_language(
    text: str,
    *,
    preference: Language | None = None,
    arbiter: CodeMixArbiter | None = None,
) -> LanguageDetection:
    """Decide which language to answer in.

    ``preference`` is the user's stored setting. It resolves Devanagari ambiguity
    (Hindi and Marathi share a script) and decides when the text carries no signal.
    """
    shares = script_shares(text)
    if not shares:
        return LanguageDetection(
            language=preference or Language.ENGLISH,
            method=DetectionMethod.PREFERENCE if preference else DetectionMethod.DEFAULT,
            confidence=0.3,
            shares=shares,
        )

    indic = {s: v for s, v in shares.items() if s != "LATIN"}
    if indic:
        top_script, top_share = max(indic.items(), key=lambda kv: kv[1])
        if top_share >= DOMINANT_SCRIPT_SHARE:
            language = SCRIPT_TO_LANGUAGE[top_script]
            # Devanagari cannot distinguish Hindi from Marathi; the user's own setting can.
            if language is Language.HINDI and preference is Language.MARATHI:
                language = Language.MARATHI
            latin_share = shares.get("LATIN", 0.0)
            mixed = latin_share >= CODE_MIX_SHARE
            return LanguageDetection(
                language=language,
                method=DetectionMethod.SCRIPT,
                confidence=round(top_share, 3),
                code_mixed=mixed,
                secondary=Language.ENGLISH if mixed else None,
                shares=shares,
            )

    return _detect_romanized(text, preference=preference, arbiter=arbiter, shares=shares)


def _detect_romanized(
    text: str,
    *,
    preference: Language | None,
    arbiter: CodeMixArbiter | None,
    shares: Mapping[str, float],
) -> LanguageDetection:
    """Latin-dominant text: English, Tanglish or Hinglish."""
    letters = sum(1 for c in text if c.isalpha())
    candidates = [Language.TAMIL, Language.HINDI]
    hits = {lang: markers_in(text, lang) for lang in candidates}
    counts = {lang: len(v) for lang, v in hits.items()}
    best = max(counts, key=lambda lang: counts[lang])

    if letters < MIN_ROMANIZED_LETTERS and counts[best] == 0:
        return LanguageDetection(
            language=preference or Language.ENGLISH,
            method=DetectionMethod.PREFERENCE if preference else DetectionMethod.DEFAULT,
            confidence=0.3,
            shares=shares,
        )

    if counts[best] == 0:
        return LanguageDetection(
            language=Language.ENGLISH,
            method=DetectionMethod.ROMANIZED_LEXICON,
            confidence=0.7,
            shares=shares,
        )

    tied = counts[Language.TAMIL] == counts[Language.HINDI]
    if tied and arbiter is not None:
        chosen = arbiter.arbitrate(text, candidates)
        if chosen is not None:
            return LanguageDetection(
                language=chosen,
                method=DetectionMethod.ARBITER,
                confidence=0.6,
                code_mixed=True,
                secondary=Language.ENGLISH,
                shares=shares,
                matched_markers=hits[chosen],
            )
    if tied:
        return LanguageDetection(
            language=preference or Language.ENGLISH,
            method=DetectionMethod.PREFERENCE if preference else DetectionMethod.DEFAULT,
            confidence=0.4,
            code_mixed=True,
            shares=shares,
        )

    # Romanized Indic text is mixed by construction; how *much* of it the lexicon
    # recognised is what the reported confidence reflects.
    token_count = max(len(tokens(text)), 1)
    marker_share = counts[best] / token_count
    return LanguageDetection(
        language=best,
        method=DetectionMethod.ROMANIZED_LEXICON,
        confidence=round(min(0.5 + marker_share, 0.9), 3),
        code_mixed=True,
        secondary=Language.ENGLISH,
        shares=shares,
        matched_markers=hits[best],
    )


def resolve_conversation_language(
    detection: LanguageDetection,
    *,
    pinned: Language | None,
    repin_threshold: float = 0.8,
) -> tuple[Language, bool]:
    """Decide the reply language for a turn, given what the conversation already settled on.

    Pinning matters in practice. Mid-conversation a fisherman may answer "seri" or "yes" —
    one ambiguous word that must not switch the whole conversation into English. A pinned
    language is therefore kept unless the new turn is *confidently* in a different one,
    which in practice means the script said so.

    Returns ``(language, changed)``.
    """
    if pinned is None:
        return detection.language, True
    if detection.language == pinned:
        return pinned, False
    if detection.confidence >= repin_threshold and detection.method in {
        DetectionMethod.SCRIPT,
        DetectionMethod.PROVIDER,
    }:
        return detection.language, True
    return pinned, False
