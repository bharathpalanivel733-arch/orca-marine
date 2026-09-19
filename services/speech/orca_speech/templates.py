"""Deterministic response templating (PLAN.md Phase 7.5).

Every sentence ORCA speaks is a **pre-translated template with slots filled from kernel
output**. Nothing is generated at runtime, in any language.

The reason is narrow and load-bearing. Free translation — by an LLM or by a live MT call —
reorders and re-renders numbers. "Hs 2.5 m" comes back as "about two and a half metres",
"2,5 m", or with the figure dropped entirely when the sentence is rephrased. On an audio
channel there is no visible text to check it against, so a mangled number is simply what
the fisherman hears. Templates make that failure impossible rather than unlikely: the slot
values are strings the kernels produced, and the surrounding words are fixed.

**How translations are produced.** Offline, at authoring time, with IndicTrans2 as a
first-pass draft and human review before a template enters this catalogue. That process
happens in a workflow, not in this module — at runtime there is no translator, and a
language with no entry for a template raises rather than silently improvising.

**What the LLM may do.** Choose which template fits the situation, and nothing else. It
never writes the sentence, never fills a numeric slot and never translates. The slot
values come from :class:`~orca_kernels.contract.KernelResult` objects by way of the
formatters here.

Slot names are validated on render, so renaming a slot in one language and forgetting
another is a test failure rather than a blank in the audio.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from string import Formatter

from orca_speech.languages import Language
from orca_speech.normalize import NormalizedSpeech, normalize_for_speech

CATALOGUE_VERSION = "1.0.0"


class TemplateId(StrEnum):
    """The sentences ORCA can say. Adding one is a catalogue change, not a prompt change."""

    SAFETY_SAFE = "safety.safe"
    SAFETY_CAUTION = "safety.caution"
    SAFETY_AVOID = "safety.avoid"
    ZONE_RECOMMENDATION = "zone.recommendation"
    GEOFENCE_APPROACHING = "geofence.approaching"
    GEOFENCE_CROSSED = "geofence.crossed"
    ABSTAIN_STALE = "abstain.stale"
    ABSTAIN_INSUFFICIENT = "abstain.insufficient"
    ABSTAIN_CONFLICT = "abstain.conflict"
    ALERT_CYCLONE = "alert.cyclone"
    ALERT_HIGH_WAVE = "alert.high_wave"
    ALERT_RETURN_TO_SHORE = "alert.return_to_shore"


class TranslationMethod(StrEnum):
    """How a template's wording in a given language came to be.

    Recorded per template so the catalogue can state honestly which strings have been
    reviewed by a speaker and which are still machine drafts.
    """

    SOURCE = "source"
    INDICTRANS2_REVIEWED = "indictrans2_reviewed"
    INDICTRANS2_DRAFT = "indictrans2_draft"


class MissingTranslationError(LookupError):
    """No wording exists for this template in this language.

    Raised rather than falling back silently: an English sentence spoken by a Tamil voice
    is worse than a handled failure, and the caller is the right place to decide.
    """


class MissingSlotError(KeyError):
    """A template slot had no value. Never filled with a placeholder or an empty string."""


@dataclass(frozen=True)
class SpeechTemplate:
    """One sentence in every language ORCA can say it in."""

    template_id: TemplateId
    slots: frozenset[str]
    forms: Mapping[Language, str]
    methods: Mapping[Language, TranslationMethod]

    def languages(self) -> frozenset[Language]:
        return frozenset(self.forms)

    def form(self, language: Language) -> str:
        try:
            return self.forms[language]
        except KeyError as exc:
            msg = f"template {self.template_id.value} has no wording for {language.value}"
            raise MissingTranslationError(msg) from exc


def _slots(template: str) -> frozenset[str]:
    return frozenset(
        name for _, name, _, _ in Formatter().parse(template) if name is not None
    )


def _template(
    template_id: TemplateId,
    *,
    en: str,
    ta: str,
    hi: str,
    reviewed: bool = True,
) -> SpeechTemplate:
    """Build a template and derive its slot set from the English source form.

    Deriving rather than declaring means a slot added to one language and not another is
    caught by :func:`validate_catalogue` instead of appearing as a gap in speech.
    """
    method = (
        TranslationMethod.INDICTRANS2_REVIEWED
        if reviewed
        else TranslationMethod.INDICTRANS2_DRAFT
    )
    return SpeechTemplate(
        template_id=template_id,
        slots=_slots(en),
        forms={Language.ENGLISH: en, Language.TAMIL: ta, Language.HINDI: hi},
        methods={
            Language.ENGLISH: TranslationMethod.SOURCE,
            Language.TAMIL: method,
            Language.HINDI: method,
        },
    )


# The catalogue.
#
# Tamil and Hindi wordings are IndicTrans2 drafts that have NOT yet been reviewed by a
# native speaker — they are marked ``reviewed=False`` and PROGRESS.md records this as an
# open gap. The structure is correct and the numbers are safe; the phrasing needs a
# speaker's eye before a jury hears it.
CATALOGUE: Mapping[TemplateId, SpeechTemplate] = {
    t.template_id: t
    for t in (
        _template(
            TemplateId.SAFETY_SAFE,
            en=(
                "Conditions are safe for your {vessel_class}. "
                "Wave height {wave_height}, wind {wind_speed}. "
                "Safety score {score} out of 100. Data is {data_age} old."
            ),
            ta=(
                "உங்கள் {vessel_class} படகுக்கு கடல் நிலை பாதுகாப்பானது. "
                "அலை உயரம் {wave_height}, காற்று {wind_speed}. "
                "பாதுகாப்பு மதிப்பெண் 100 இல் {score}. தரவின் வயது {data_age}."
            ),
            hi=(
                "आपकी {vessel_class} नाव के लिए स्थिति सुरक्षित है। "
                "लहर की ऊंचाई {wave_height}, हवा {wind_speed}। "
                "सुरक्षा स्कोर 100 में से {score}। डेटा {data_age} पुराना है।"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.SAFETY_CAUTION,
            en=(
                "Take care. Conditions are marginal for your {vessel_class}. "
                "Wave height {wave_height}, wind {wind_speed}. "
                "Safety score {score} out of 100. {reason}"
            ),
            ta=(
                "கவனமாக இருங்கள். உங்கள் {vessel_class} படகுக்கு கடல் நிலை எல்லைக்கோட்டில் உள்ளது. "
                "அலை உயரம் {wave_height}, காற்று {wind_speed}. "
                "பாதுகாப்பு மதிப்பெண் 100 இல் {score}. {reason}"
            ),
            hi=(
                "सावधान रहें। आपकी {vessel_class} नाव के लिए स्थिति सीमा पर है। "
                "लहर की ऊंचाई {wave_height}, हवा {wind_speed}। "
                "सुरक्षा स्कोर 100 में से {score}। {reason}"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.SAFETY_AVOID,
            en=(
                "Do not go out. Conditions are unsafe for your {vessel_class}. "
                "Wave height {wave_height}, wind {wind_speed}. "
                "Safety score {score} out of 100. {reason}"
            ),
            ta=(
                "கடலுக்கு செல்ல வேண்டாம். உங்கள் {vessel_class} படகுக்கு கடல் நிலை பாதுகாப்பற்றது. "
                "அலை உயரம் {wave_height}, காற்று {wind_speed}. "
                "பாதுகாப்பு மதிப்பெண் 100 இல் {score}. {reason}"
            ),
            hi=(
                "समुद्र में न जाएं। आपकी {vessel_class} नाव के लिए स्थिति असुरक्षित है। "
                "लहर की ऊंचाई {wave_height}, हवा {wind_speed}। "
                "सुरक्षा स्कोर 100 में से {score}। {reason}"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.ZONE_RECOMMENDATION,
            en=(
                "Option {rank}: fishing ground {bearing} from {origin}, {distance} away. "
                "Travel time {travel_time}, safety score {score} out of 100. "
                "This advisory was issued {advisory_age} ago."
            ),
            ta=(
                "தேர்வு {rank}: {origin} இலிருந்து {bearing} திசையில், {distance} தொலைவில் மீன்பிடி இடம். "
                "பயண நேரம் {travel_time}, பாதுகாப்பு மதிப்பெண் 100 இல் {score}. "
                "இந்த அறிவிப்பு {advisory_age} முன்பு வெளியிடப்பட்டது."
            ),
            hi=(
                "विकल्प {rank}: {origin} से {bearing} दिशा में, {distance} दूर मछली पकड़ने का स्थान। "
                "यात्रा समय {travel_time}, सुरक्षा स्कोर 100 में से {score}। "
                "यह सलाह {advisory_age} पहले जारी की गई थी।"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.GEOFENCE_APPROACHING,
            en=(
                "Warning. You are {distance} from the {boundary} and closing. "
                "At your present course and speed you will reach it in {time_to_boundary}. "
                "Turn now."
            ),
            ta=(
                "எச்சரிக்கை. நீங்கள் {boundary} எல்லையிலிருந்து {distance} தொலைவில் உள்ளீர்கள், நெருங்குகிறீர்கள். "
                "தற்போதைய திசை மற்றும் வேகத்தில் {time_to_boundary} இல் அதை அடைவீர்கள். "
                "இப்போதே திரும்புங்கள்."
            ),
            hi=(
                "चेतावनी। आप {boundary} सीमा से {distance} दूर हैं और पास आ रहे हैं। "
                "वर्तमान दिशा और गति पर आप {time_to_boundary} में वहां पहुंच जाएंगे। "
                "अभी मुड़ें।"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.GEOFENCE_CROSSED,
            en=(
                "You have crossed the {boundary}. You are {distance} beyond it. "
                "Turn back towards {safe_bearing} immediately."
            ),
            ta=(
                "நீங்கள் {boundary} எல்லையைக் கடந்துவிட்டீர்கள். அதற்கு அப்பால் {distance} தொலைவில் உள்ளீர்கள். "
                "உடனடியாக {safe_bearing} திசையில் திரும்புங்கள்."
            ),
            hi=(
                "आपने {boundary} सीमा पार कर ली है। आप उससे {distance} आगे हैं। "
                "तुरंत {safe_bearing} दिशा में वापस मुड़ें।"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.ABSTAIN_STALE,
            en=(
                "I cannot safely answer that. The nearest reliable data is {data_age} old, "
                "and I do not guess about sea conditions. Please try again after {retry_after}."
            ),
            ta=(
                "என்னால் பாதுகாப்பாக பதிலளிக்க முடியாது. கிடைக்கும் நம்பகமான தரவு {data_age} பழையது, "
                "கடல் நிலை குறித்து நான் ஊகிக்க மாட்டேன். {retry_after} கழித்து மீண்டும் முயற்சிக்கவும்."
            ),
            hi=(
                "मैं सुरक्षित रूप से उत्तर नहीं दे सकता। निकटतम विश्वसनीय डेटा {data_age} पुराना है, "
                "और मैं समुद्र की स्थिति का अनुमान नहीं लगाता। {retry_after} बाद फिर से प्रयास करें।"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.ABSTAIN_INSUFFICIENT,
            en=(
                "I cannot safely answer that. I have only {evidence_count} sources for "
                "{location}, and a safety verdict needs more. {remedy}"
            ),
            ta=(
                "என்னால் பாதுகாப்பாக பதிலளிக்க முடியாது. {location} பகுதிக்கு {evidence_count} ஆதாரங்கள் "
                "மட்டுமே உள்ளன, பாதுகாப்பு முடிவுக்கு இது போதாது. {remedy}"
            ),
            hi=(
                "मैं सुरक्षित रूप से उत्तर नहीं दे सकता। {location} के लिए मेरे पास केवल "
                "{evidence_count} स्रोत हैं, सुरक्षा निर्णय के लिए यह पर्याप्त नहीं है। {remedy}"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.ABSTAIN_CONFLICT,
            en=(
                "Sources disagree about {variable} at {location}: {source_a} reports "
                "{value_a} and {source_b} reports {value_b}. I will not pick one for you. "
                "Treat conditions as the worse of the two."
            ),
            ta=(
                "{location} பகுதியில் {variable} குறித்து ஆதாரங்கள் முரண்படுகின்றன: {source_a} "
                "{value_a} எனவும், {source_b} {value_b} எனவும் கூறுகிறது. நான் ஒன்றைத் தேர்ந்தெடுக்க மாட்டேன். "
                "இரண்டில் மோசமானதை கணக்கில் கொள்ளுங்கள்."
            ),
            hi=(
                "{location} पर {variable} को लेकर स्रोत असहमत हैं: {source_a} {value_a} "
                "बताता है और {source_b} {value_b} बताता है। मैं आपके लिए एक नहीं चुनूंगा। "
                "दोनों में से खराब स्थिति मानकर चलें।"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.ALERT_CYCLONE,
            en=(
                "Cyclone warning. {system_name} is {distance} from {location}, "
                "moving {bearing} at {speed}. Return to harbour immediately. "
                "Issued by {authority} at {issued_time}."
            ),
            ta=(
                "புயல் எச்சரிக்கை. {system_name} {location} இலிருந்து {distance} தொலைவில் உள்ளது, "
                "{bearing} திசையில் {speed} வேகத்தில் நகர்கிறது. உடனடியாக துறைமுகம் திரும்புங்கள். "
                "{authority} {issued_time} இல் வெளியிட்டது."
            ),
            hi=(
                "चक्रवात चेतावनी। {system_name} {location} से {distance} दूर है, "
                "{bearing} दिशा में {speed} की गति से बढ़ रहा है। तुरंत बंदरगाह लौटें। "
                "{authority} द्वारा {issued_time} पर जारी।"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.ALERT_HIGH_WAVE,
            en=(
                "High wave alert for {location}. Waves up to {wave_height} expected "
                "until {valid_until}. Do not put out to sea. Issued by {authority}."
            ),
            ta=(
                "{location} பகுதிக்கு உயர் அலை எச்சரிக்கை. {valid_until} வரை {wave_height} வரை "
                "அலைகள் எதிர்பார்க்கப்படுகின்றன. கடலுக்கு செல்ல வேண்டாம். {authority} வெளியிட்டது."
            ),
            hi=(
                "{location} के लिए ऊंची लहर चेतावनी। {valid_until} तक {wave_height} तक "
                "लहरें अपेक्षित हैं। समुद्र में न जाएं। {authority} द्वारा जारी।"
            ),
            reviewed=False,
        ),
        _template(
            TemplateId.ALERT_RETURN_TO_SHORE,
            en=(
                "Return to shore now. Conditions at {location} are deteriorating: "
                "wave height {wave_height}, wind {wind_speed}. "
                "Nearest harbour is {harbour}, {distance} away."
            ),
            ta=(
                "இப்போதே கரைக்குத் திரும்புங்கள். {location} பகுதியில் நிலை மோசமடைகிறது: "
                "அலை உயரம் {wave_height}, காற்று {wind_speed}. "
                "அருகிலுள்ள துறைமுகம் {harbour}, {distance} தொலைவில்."
            ),
            hi=(
                "अभी किनारे लौटें। {location} पर स्थिति बिगड़ रही है: "
                "लहर की ऊंचाई {wave_height}, हवा {wind_speed}। "
                "निकटतम बंदरगाह {harbour} है, {distance} दूर।"
            ),
            reviewed=False,
        ),
    )
}


@dataclass(frozen=True)
class RenderedMessage:
    """A filled template, ready to display and to speak.

    Carries its own provenance — template id, catalogue version, the slot values used —
    so the spoken sentence is as traceable as the number inside it.
    """

    text: str
    language: Language
    template_id: TemplateId
    catalogue_version: str
    slots: Mapping[str, str]
    requested_language: Language
    reviewed: bool

    @property
    def used_fallback_language(self) -> bool:
        return self.language != self.requested_language


def validate_catalogue(catalogue: Mapping[TemplateId, SpeechTemplate] = CATALOGUE) -> None:
    """Assert every language of every template declares exactly the same slots.

    Run as a test. A slot present in English but dropped from the Tamil wording would
    leave a silent hole in spoken Tamil output — the number would simply not be said.
    """
    for template_id, template in catalogue.items():
        for language, form in template.forms.items():
            found = _slots(form)
            if found != template.slots:
                missing = sorted(template.slots - found)
                extra = sorted(found - template.slots)
                msg = (
                    f"{template_id.value} [{language.value}] slot mismatch: "
                    f"missing={missing} unexpected={extra}"
                )
                raise ValueError(msg)


def render(
    template_id: TemplateId,
    language: Language,
    slots: Mapping[str, str],
    *,
    fallback_language: Language | None = Language.ENGLISH,
    catalogue: Mapping[TemplateId, SpeechTemplate] = CATALOGUE,
) -> RenderedMessage:
    """Fill a template. No text is generated; every word comes from the catalogue.

    A language with no wording falls back to ``fallback_language`` and says so in the
    result, so the UI can show "answered in English" rather than pretending. Passing
    ``fallback_language=None`` makes a missing translation an error instead.
    """
    template = catalogue[template_id]
    missing = sorted(template.slots - set(slots))
    if missing:
        msg = f"{template_id.value} missing slot value(s): {', '.join(missing)}"
        raise MissingSlotError(msg)

    used_language = language
    try:
        form = template.form(language)
    except MissingTranslationError:
        if fallback_language is None:
            raise
        form = template.form(fallback_language)
        used_language = fallback_language

    values = {name: slots[name] for name in template.slots}
    return RenderedMessage(
        text=form.format(**values),
        language=used_language,
        template_id=template_id,
        catalogue_version=CATALOGUE_VERSION,
        slots=values,
        requested_language=language,
        reviewed=template.methods.get(used_language) is not TranslationMethod.INDICTRANS2_DRAFT,
    )


def render_for_speech(
    template_id: TemplateId,
    language: Language,
    slots: Mapping[str, str],
    *,
    decimal_as_word: bool = False,
    catalogue: Mapping[TemplateId, SpeechTemplate] = CATALOGUE,
) -> tuple[RenderedMessage, NormalizedSpeech]:
    """Render, then normalize for synthesis.

    Returns both because they are different artifacts with different jobs: the rendered
    text is what the transcript shows, and the normalized text is what the voice reads.
    Keeping them separate is what makes the visible transcript a genuine check on the
    audio rather than a paraphrase of it.
    """
    message = render(template_id, language, slots, catalogue=catalogue)
    spoken = normalize_for_speech(
        message.text, message.language, decimal_as_word=decimal_as_word
    )
    return message, spoken


def format_measurement(value: float, unit: str, *, decimals: int = 1) -> str:
    """Format a kernel number for a slot.

    The single place a float becomes a string on the speech path. Rounding happens here,
    once, visibly — not inside a template and never inside a model.
    """
    return f"{value:.{decimals}f} {unit}"


def format_duration(seconds: float) -> str:
    """Format an elapsed or remaining time for a slot, in units a person uses."""
    if seconds < 90:
        return f"{round(seconds)} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min"
    return f"{minutes / 60:.1f} h"
