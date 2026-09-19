"""Alert audio, generated at alert creation time (PLAN.md Phase 7.9).

A proactive cyclone warning that arrives as text is useless to the person it most needs to
reach. So the audio is synthesized **when the alert is created**, in the recipient's
language, and travels attached to the push payload — not fetched by the device afterwards,
which is the one moment the device may have no signal.

Two properties follow from that timing and both matter:

* **The clip is cached before it is ever needed.** Alert wording comes from a fixed
  template set, so the pre-generation job (:mod:`orca_speech.pregenerate`) has already
  synthesized every standard phrase. Creating an alert is then a cache hit even when
  Bhashini is unreachable.
* **The audio and the text say the same thing.** Both come from one
  :class:`~orca_speech.templates.RenderedMessage`, with the numbers in the slots put there
  by kernels. The push payload carries both, so a recipient who reads and a recipient who
  listens receive the same warning — a property that free-form narration could not
  guarantee.

This module produces the audio and the payload fragment. Delivery — CAP XML, FCM, the
WebSocket channel — is Phase 8 and is deliberately not implemented here.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from orca_speech.cache import AudioCacheKey
from orca_speech.languages import Language
from orca_speech.normalize import NormalizedSpeech
from orca_speech.providers import AllProvidersFailedError, ProviderTier
from orca_speech.templates import (
    RenderedMessage,
    TemplateId,
    render_for_speech,
)
from orca_speech.tts import SynthesisRequest, TtsChain, VoiceGender, select_voice

# Templates that may be used for an unsolicited push. Restricting the set is a safety
# measure, not bookkeeping: an alert interrupts someone at sea, and only a warning earns
# that. A zone recommendation does not get to wake anybody up.
ALERT_TEMPLATES: frozenset[TemplateId] = frozenset(
    {
        TemplateId.ALERT_CYCLONE,
        TemplateId.ALERT_HIGH_WAVE,
        TemplateId.ALERT_RETURN_TO_SHORE,
        TemplateId.GEOFENCE_APPROACHING,
        TemplateId.GEOFENCE_CROSSED,
    }
)


class NotAnAlertTemplateError(ValueError):
    """A template outside :data:`ALERT_TEMPLATES` was used for a push."""


@dataclass(frozen=True)
class AlertAudio:
    """A synthesized alert, ready to attach to a notification."""

    message: RenderedMessage
    spoken: NormalizedSpeech
    audio: bytes
    cache_key: AudioCacheKey
    content_type: str
    provider: str
    tier: ProviderTier
    cache_hit: bool
    created_at: datetime

    @property
    def size_bytes(self) -> int:
        return len(self.audio)

    def push_payload(self, *, inline_audio: bool = True) -> dict[str, Any]:
        """The notification fragment carrying both readings of the warning.

        ``inline_audio`` base64-encodes the clip into the payload so it arrives with the
        notification. That is the right default for the at-sea case and the wrong one for
        a long clip on a metered link, so the caller can switch to referencing the cached
        object instead.
        """
        payload: dict[str, Any] = {
            "language": self.message.language.value,
            "text": self.message.text,
            "spoken_text": self.spoken.text,
            "template_id": self.message.template_id.value,
            "catalogue_version": self.message.catalogue_version,
            "audio_cache_key": self.cache_key.object_key,
            "audio_content_type": self.content_type,
            "audio_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat(),
            "voice_source": self.provider,
            "translation_reviewed": self.message.reviewed,
        }
        if inline_audio:
            payload["audio_base64"] = base64.b64encode(self.audio).decode("ascii")
        return payload


def synthesize_alert(
    template_id: TemplateId,
    language: Language,
    slots: Mapping[str, str],
    *,
    chain: TtsChain,
    created_at: datetime,
    gender: VoiceGender = VoiceGender.FEMALE,
    speed: float = 1.0,
) -> AlertAudio:
    """Render an alert and speak it, in one step.

    Raises :class:`NotAnAlertTemplateError` for a non-alert template, and
    :class:`~orca_speech.providers.AllProvidersFailedError` if nothing — including the
    pre-generated cache — can produce the audio. Neither is swallowed: a silent alert
    should be a visible failure to whatever is sending it, so the text-only fallback is a
    decision the caller makes knowingly (see :func:`alert_or_text_only`).
    """
    if template_id not in ALERT_TEMPLATES:
        msg = f"{template_id.value} is not an alert template"
        raise NotAnAlertTemplateError(msg)

    message, spoken = render_for_speech(template_id, language, slots)
    voice = select_voice(message.language, gender)
    request = SynthesisRequest(
        text=spoken.text, language=message.language, voice=voice, speed=speed
    )
    result = chain.synthesize(request)

    return AlertAudio(
        message=message,
        spoken=spoken,
        audio=result.audio,
        cache_key=result.cache_key,
        content_type=result.content_type,
        provider=result.provider,
        tier=result.tier,
        cache_hit=result.cache_hit,
        created_at=created_at,
    )


def alert_or_text_only(
    template_id: TemplateId,
    language: Language,
    slots: Mapping[str, str],
    *,
    chain: TtsChain,
    created_at: datetime,
    gender: VoiceGender = VoiceGender.FEMALE,
) -> dict[str, Any]:
    """Build a push payload, degrading to text if no audio can be produced.

    The warning goes out either way — a cyclone alert is not worth withholding because a
    voice service is down — but the payload says ``audio_available: false`` so the client
    shows the text prominently rather than waiting for a clip that will never arrive.
    """
    try:
        audio = synthesize_alert(
            template_id, language, slots, chain=chain, created_at=created_at, gender=gender
        )
    except AllProvidersFailedError as exc:
        message, spoken = render_for_speech(template_id, language, slots)
        return {
            "language": message.language.value,
            "text": message.text,
            "spoken_text": spoken.text,
            "template_id": message.template_id.value,
            "catalogue_version": message.catalogue_version,
            "created_at": created_at.isoformat(),
            "audio_available": False,
            "audio_failure": [
                {"provider": a.provider, "outcome": a.outcome.value, "detail": a.detail}
                for a in exc.attempts
            ],
        }
    return {**audio.push_payload(), "audio_available": True}
