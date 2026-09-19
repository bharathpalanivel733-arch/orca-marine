"""ORCA multilingual voice I/O (PLAN.md Phase 7).

Voice is the delivery channel, not a feature. The user is often at sea, frequently
low-literacy, and holding a phone in one hand — so the answer has to be spoken, in their
language, and it has to be the *same* answer the screen shows.

Three rules hold the design together, and each is enforced structurally rather than by
convention:

1. **No model writes a sentence.** Every utterance is a pre-translated template
   (:mod:`orca_speech.templates`) with slots filled from kernel output.
2. **No number changes on the audio path.** :mod:`orca_speech.normalize` expands units and
   symbols and verifies that the numeric tokens are byte-identical before and after,
   raising :class:`~orca_speech.normalize.NumericDriftError` if they are not.
3. **A provider outage degrades the voice, never the answer.** Both chains fall through a
   documented hierarchy and report which tier spoke, and the pre-generated cache
   (:mod:`orca_speech.cache`) means the rehearsed script plays with the network down.

Tamil, Hindi and English are the rehearsed paths; the other seven languages are enabled and
smoke-tested. The Tamil and Hindi template wordings are IndicTrans2 drafts awaiting native
review — see PROGRESS.md, which records that as an open gap rather than a finished claim.
"""

from orca_speech.alerts import (
    ALERT_TEMPLATES,
    AlertAudio,
    NotAnAlertTemplateError,
    alert_or_text_only,
    synthesize_alert,
)
from orca_speech.asr import (
    CONFIRM_BELOW_CONFIDENCE,
    MAX_AUDIO_SECONDS,
    AsrChain,
    AsrProvider,
    AsrResult,
    AudioClip,
    BhashiniAsr,
    BrowserAsr,
    IndicWhisperAsr,
    Transcript,
)
from orca_speech.cache import (
    AudioCache,
    AudioCacheKey,
    CachedAudio,
    CoverageReport,
    InMemoryAudioCache,
    ObjectStoreAudioCache,
    has_unfilled_slots,
    key_set,
)
from orca_speech.detect import (
    CODE_MIX_SHARE,
    DOMINANT_SCRIPT_SHARE,
    CodeMixArbiter,
    DetectionMethod,
    LanguageDetection,
    LexiconArbiter,
    detect_language,
    resolve_conversation_language,
)
from orca_speech.gazetteer import (
    COORDINATE_PRECISION_NOTE,
    PLACES,
    Place,
    PlaceKind,
    PlaceResolution,
    ResolutionKind,
    find_places_in,
    normalize_name,
    resolve_place,
)
from orca_speech.languages import (
    PROFILES,
    REHEARSED,
    Language,
    LanguageProfile,
    profile_for,
    script_shares,
)
from orca_speech.normalize import (
    UNIT_TABLE,
    NormalizedSpeech,
    NumericDriftError,
    extract_numbers,
    normalize_for_speech,
    spoken_unit,
)
from orca_speech.pregenerate import (
    DEMO_SCRIPT,
    ScriptedLine,
    audit_script,
    pregenerate,
    scripted_texts,
)
from orca_speech.providers import (
    AllProvidersFailedError,
    ProviderAttempt,
    ProviderOutcome,
    ProviderTier,
    ProviderUnavailableError,
)
from orca_speech.templates import (
    CATALOGUE,
    CATALOGUE_VERSION,
    MissingSlotError,
    MissingTranslationError,
    RenderedMessage,
    SpeechTemplate,
    TemplateId,
    TranslationMethod,
    format_duration,
    format_measurement,
    render,
    render_for_speech,
    validate_catalogue,
)
from orca_speech.tts import (
    VOICES,
    BhashiniTts,
    IndicParlerTts,
    PreGeneratedTts,
    SynthesisRequest,
    SynthesisResult,
    TtsChain,
    TtsProvider,
    Voice,
    VoiceGender,
    build_default_chain,
    select_voice,
)

SERVICE_NAME = "orca-speech"
__version__ = "0.1.0"

__all__ = [
    "ALERT_TEMPLATES",
    "CATALOGUE",
    "CATALOGUE_VERSION",
    "CODE_MIX_SHARE",
    "CONFIRM_BELOW_CONFIDENCE",
    "COORDINATE_PRECISION_NOTE",
    "DEMO_SCRIPT",
    "DOMINANT_SCRIPT_SHARE",
    "MAX_AUDIO_SECONDS",
    "PLACES",
    "PROFILES",
    "REHEARSED",
    "SERVICE_NAME",
    "UNIT_TABLE",
    "VOICES",
    "AlertAudio",
    "AllProvidersFailedError",
    "AsrChain",
    "AsrProvider",
    "AsrResult",
    "AudioCache",
    "AudioCacheKey",
    "AudioClip",
    "BhashiniAsr",
    "BhashiniTts",
    "BrowserAsr",
    "CachedAudio",
    "CodeMixArbiter",
    "CoverageReport",
    "DetectionMethod",
    "InMemoryAudioCache",
    "IndicParlerTts",
    "IndicWhisperAsr",
    "Language",
    "LanguageDetection",
    "LanguageProfile",
    "LexiconArbiter",
    "MissingSlotError",
    "MissingTranslationError",
    "NormalizedSpeech",
    "NotAnAlertTemplateError",
    "NumericDriftError",
    "ObjectStoreAudioCache",
    "Place",
    "PlaceKind",
    "PlaceResolution",
    "PreGeneratedTts",
    "ProviderAttempt",
    "ProviderOutcome",
    "ProviderTier",
    "ProviderUnavailableError",
    "RenderedMessage",
    "ResolutionKind",
    "ScriptedLine",
    "SpeechTemplate",
    "SynthesisRequest",
    "SynthesisResult",
    "TemplateId",
    "Transcript",
    "TranslationMethod",
    "TtsChain",
    "TtsProvider",
    "Voice",
    "VoiceGender",
    "__version__",
    "alert_or_text_only",
    "audit_script",
    "build_default_chain",
    "detect_language",
    "extract_numbers",
    "find_places_in",
    "format_duration",
    "format_measurement",
    "has_unfilled_slots",
    "key_set",
    "normalize_for_speech",
    "normalize_name",
    "pregenerate",
    "profile_for",
    "render",
    "render_for_speech",
    "resolve_conversation_language",
    "resolve_place",
    "script_shares",
    "scripted_texts",
    "select_voice",
    "spoken_unit",
    "synthesize_alert",
    "validate_catalogue",
]
