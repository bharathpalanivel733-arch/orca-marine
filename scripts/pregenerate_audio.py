"""Build-time audio pre-generation (PLAN.md Phase 7.7).

Run before a demo, and in CI when the template catalogue changes:

    python scripts/pregenerate_audio.py                 # report what would be cached
    python scripts/pregenerate_audio.py --write         # synthesize and store

Without ``--write`` it is read-only: it renders the script, checks that every line still
fills cleanly, and prints the cache keys that would be produced. That alone catches the
failure this job exists to prevent — a template that gained a slot the demo script was
never updated for — without needing a synthesizer at all.

**With no Bhashini key and no self-hosted weights, ``--write`` will cache nothing** and say
so, per language and per phrase. That is an honest report, not a silent success: if the
coverage is zero on the morning of the demo, the fallback has nothing to fall back to, and
this output is where that becomes visible.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from orca_speech import (
    REHEARSED,
    AudioCacheKey,
    BhashiniTts,
    IndicParlerTts,
    InMemoryAudioCache,
    Language,
    ObjectStoreAudioCache,
    TtsChain,
    VoiceGender,
    audit_script,
    pregenerate,
    scripted_texts,
    select_voice,
)
from orca_speech.cache import DEFAULT_BUCKET


def _force_utf8_output() -> None:
    """Print Tamil and Devanagari on a Windows console.

    Windows still defaults stdout to cp1252, which raises ``UnicodeEncodeError`` on the
    first Tamil phrase — so the tool would crash precisely on the languages it exists to
    prepare. Reconfiguring is the one-line fix; transliterating the output would hide what
    is actually being cached.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def build_cache(use_object_store: bool) -> object:
    """The audio cache to write into.

    MinIO in a real run; in-memory when the object store is not configured, so a dry run
    still exercises the whole path on a laptop.
    """
    if not use_object_store:
        return InMemoryAudioCache()

    from orca_ingest.storage.objects import build_s3_client, ensure_bucket

    client = build_s3_client(
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000"),
        # These names must match .env.example and orca_ingest's object-store config. An
        # unrecognised name falls back to a default that cannot authenticate, and the job
        # fails at upload time rather than at startup.
        access_key=os.environ.get("S3_ACCESS_KEY_ID", "orca-minio"),
        secret_key=os.environ.get("S3_SECRET_ACCESS_KEY", "orca-minio-secret"),
    )
    bucket = os.environ.get("SPEECH_AUDIO_BUCKET", DEFAULT_BUCKET)
    ensure_bucket(client, bucket)
    return ObjectStoreAudioCache(client, bucket)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Actually synthesize and store the clips."
    )
    parser.add_argument(
        "--object-store",
        action="store_true",
        help="Write to MinIO/S3 rather than an in-process cache.",
    )
    parser.add_argument(
        "--all-languages",
        action="store_true",
        help="Include the seven smoke-tested languages, not only the rehearsed three.",
    )
    args = parser.parse_args(argv)
    _force_utf8_output()

    problems = audit_script()
    if problems:
        print("Demo script does not render cleanly:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    languages = list(Language) if args.all_languages else sorted(REHEARSED)

    if not args.write:
        print("Dry run. Cache keys that a --write run would produce:\n")
        for language in languages:
            voice = select_voice(language, VoiceGender.FEMALE)
            for text in scripted_texts(language):
                key = AudioCacheKey.for_text(
                    text, language=language, voice=voice.voice_id, speed=1.0
                )
                print(f"  {key.object_key}  {text[:60]}")
        print("\nScript renders cleanly in every language checked.")
        return 0

    cache = build_cache(args.object_store)
    chain = TtsChain([BhashiniTts(), IndicParlerTts()], cache=cache)  # type: ignore[arg-type]
    reports = pregenerate(chain, cache, languages=languages)  # type: ignore[arg-type]

    failed = False
    print(f"{'language':<10} {'voice':<16} {'cached':>8} {'of':>4}")
    for report in reports:
        print(
            f"{report.language.value:<10} {report.voice:<16} "
            f"{report.cached:>8} {report.requested:>4}"
        )
        if not report.complete:
            failed = True
            for text in report.missing:
                print(f"    not cached: {text[:70]}")

    if failed:
        print(
            "\nSome phrases were not cached. With no ULCA key and no self-hosted weights "
            "this is expected — and it means the outage fallback has nothing stored.",
            file=sys.stderr,
        )
        # Exit 0: an empty cache on a machine with no credentials is a reported state, not
        # a build failure. A broken script (above) is.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
