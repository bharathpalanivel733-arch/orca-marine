"""Audio cache and pre-generation (PLAN.md Phase 7.7, §1.3).

The cache is the reason a Bhashini outage is an inconvenience rather than a failed demo.
Every string ORCA can say is synthesized ahead of time and stored, so the common path
never calls a provider at all: it is faster, it costs no quota, and it works with the
network unplugged.

**The key is the contract.** ``sha256(text) + language + voice + speed`` — every input that
changes the audio, and nothing that does not. Two consequences are load-bearing:

* Identical text in identical settings is the same object, so the pre-generation job and
  the live path address the same bytes. If the key included a timestamp or a run id, the
  pre-generated cache would never be hit and the outage protection would be imaginary.
* Different text is a different object. A template whose numbers changed — 2.5 m became
  3.1 m — hashes differently, so there is no way to serve yesterday's wave height in
  today's audio. The cache cannot go stale in a way that matters, because staleness of the
  *numbers* is staleness of the *key*.

The text hashed is the **normalized** text that the synthesizer actually receives, not the
display sentence. Hashing the display text would collide two utterances that normalize
differently, and the voice would say the wrong one.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from orca_speech.languages import Language

DEFAULT_BUCKET = "orca-audio"

# Speed is quantized before it enters the key. A float slider producing 1.0000001 would
# otherwise generate an unbounded set of near-identical objects, each a cache miss.
SPEED_DECIMALS = 2


@dataclass(frozen=True)
class AudioCacheKey:
    """Everything that determines the bytes of a synthesized clip."""

    text_sha256: str
    language: Language
    voice: str
    speed: float
    audio_format: str = "wav"

    @classmethod
    def for_text(
        cls,
        text: str,
        *,
        language: Language,
        voice: str,
        speed: float = 1.0,
        audio_format: str = "wav",
    ) -> AudioCacheKey:
        """Build a key from the text that will be synthesized.

        ``text`` must already be normalized (:mod:`orca_speech.normalize`); passing the
        display sentence instead produces a key that does not match what is spoken.
        """
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return cls(
            text_sha256=digest,
            language=language,
            voice=voice,
            speed=round(speed, SPEED_DECIMALS),
            audio_format=audio_format,
        )

    @property
    def object_key(self) -> str:
        """Path in object storage.

        Laid out language-first so a language's whole cache can be listed, counted or
        purged in one prefix scan — which is what the pre-generation job's coverage report
        actually does.
        """
        speed_tag = f"{self.speed:.{SPEED_DECIMALS}f}".replace(".", "_")
        return (
            f"{self.language.value}/{self.voice}/{speed_tag}/"
            f"{self.text_sha256}.{self.audio_format}"
        )

    def __str__(self) -> str:
        return self.object_key


@dataclass(frozen=True)
class CachedAudio:
    """A clip and where it came from."""

    audio: bytes
    key: AudioCacheKey
    content_type: str = "audio/wav"

    @property
    def size_bytes(self) -> int:
        return len(self.audio)


@runtime_checkable
class AudioCache(Protocol):
    """Storage for synthesized clips."""

    def get(self, key: AudioCacheKey) -> CachedAudio | None: ...

    def put(self, key: AudioCacheKey, audio: bytes, *, content_type: str = "audio/wav") -> None: ...

    def has(self, key: AudioCacheKey) -> bool: ...


class InMemoryAudioCache:
    """Cache for tests, for the offline bundle, and for a single-process demo.

    Not a stand-in for the object store in production — it holds every clip in the
    process — but it is the exact same interface, so the fallback chain is exercised
    identically in tests and in the field.
    """

    def __init__(self, initial: dict[AudioCacheKey, bytes] | None = None) -> None:
        self._store: dict[str, CachedAudio] = {}
        for key, audio in (initial or {}).items():
            self.put(key, audio)

    def get(self, key: AudioCacheKey) -> CachedAudio | None:
        return self._store.get(key.object_key)

    def put(self, key: AudioCacheKey, audio: bytes, *, content_type: str = "audio/wav") -> None:
        self._store[key.object_key] = CachedAudio(audio=audio, key=key, content_type=content_type)

    def has(self, key: AudioCacheKey) -> bool:
        return key.object_key in self._store

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._store))

    def __len__(self) -> int:
        return len(self._store)


class ObjectStoreAudioCache:
    """Cache backed by MinIO in development and S3 in production.

    Reuses the ingest service's client builder rather than opening a second convention
    for the same object store (PLAN.md 1.10, 7.7).
    """

    def __init__(self, client: Any, bucket: str = DEFAULT_BUCKET) -> None:
        self._client = client
        self._bucket = bucket

    def get(self, key: AudioCacheKey) -> CachedAudio | None:
        from botocore.exceptions import ClientError

        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key.object_key)
        except ClientError:
            return None
        return CachedAudio(
            audio=response["Body"].read(),
            key=key,
            content_type=response.get("ContentType", "audio/wav"),
        )

    def put(self, key: AudioCacheKey, audio: bytes, *, content_type: str = "audio/wav") -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key.object_key,
            Body=audio,
            ContentType=content_type,
        )

    def has(self, key: AudioCacheKey) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key.object_key)
        except ClientError:
            return False
        return True

    def list_language(self, language: Language) -> Iterator[str]:
        """Object keys cached for a language, for the coverage report."""
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=f"{language.value}/"):
            for item in page.get("Contents", []):
                yield str(item["Key"])


@dataclass(frozen=True)
class CoverageReport:
    """What the pre-generation job managed to cache, and what it did not.

    Reported rather than asserted. A phrase that failed to pre-generate is a phrase that
    will need a live provider at demo time, and that is worth knowing beforehand rather
    than discovering on stage.
    """

    language: Language
    voice: str
    requested: int
    cached: int
    missing: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def ratio(self) -> float:
        return self.cached / self.requested if self.requested else 1.0


_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def has_unfilled_slots(text: str) -> bool:
    """Whether a string still contains template placeholders.

    Guards the pre-generation job: caching a clip that says "wave height {wave_height}"
    would produce audio reading the brace aloud, and it would be served confidently.
    """
    return bool(_PLACEHOLDER.search(text))


def key_set(
    texts: Iterable[str],
    *,
    language: Language,
    voice: str,
    speed: float = 1.0,
    audio_format: str = "wav",
) -> tuple[AudioCacheKey, ...]:
    """Keys for a batch of already-normalized strings, de-duplicated and ordered.

    De-duplication is why this exists: a demo script repeats the same abstention sentence
    in several places, and synthesizing it once is the whole point of the cache.
    """
    seen: dict[str, AudioCacheKey] = {}
    for text in texts:
        key = AudioCacheKey.for_text(
            text, language=language, voice=voice, speed=speed, audio_format=audio_format
        )
        seen.setdefault(key.object_key, key)
    return tuple(seen[name] for name in sorted(seen))
