"use client";

/**
 * "Read aloud" control for any card (PLAN.md Phase 7.8).
 *
 * Present on every recommendation and alert, because the reason ORCA speaks at all is that
 * its user may not read comfortably — a voice available only on the answer screen would
 * miss most of what there is to say.
 *
 * Three things it is careful about:
 *
 * - **The visible transcript.** Once a clip has played, the spoken text is shown. Audio is
 *   the one output channel with no record of what it said, and without the transcript a
 *   mangled unit or a wrong number is undetectable.
 * - **Naming the voice.** A clip from the cache or the offline model is labelled as such,
 *   the same way stale data is labelled everywhere else in ORCA.
 * - **Accessibility.** A real button, keyboard reachable, with `aria-pressed` reflecting
 *   playback and `aria-live` on the status line — a voice feature that assistive
 *   technology cannot drive would be a peculiar thing to ship.
 */

import { useCallback, useEffect, useState } from "react";
import { Volume2, Pause, RotateCcw, Loader2, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  audioUrlFrom,
  synthesize,
  voiceSourceLabel,
  SpeechRequestError,
  type LanguageCode,
  type SynthesizeResponse,
} from "@/lib/speech/client";
import { playback, type PlaybackSnapshot } from "@/lib/speech/playback";

export interface ReadAloudProps {
  /** The rendered template text. Never free-form prose — see orca_speech.templates. */
  readonly text: string;
  readonly language: LanguageCode;
  /** Stable id so only this card shows a pause button while it is the one playing. */
  readonly clipId: string;
  readonly gender?: "female" | "male";
  readonly speed?: number;
  readonly compact?: boolean;
}

export function ReadAloud({
  text,
  language,
  clipId,
  gender = "female",
  speed = 1.0,
  compact = false,
}: ReadAloudProps) {
  const [loading, setLoading] = useState(false);
  const [clip, setClip] = useState<SynthesizeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<PlaybackSnapshot>(() => playback.snapshot());

  useEffect(() => playback.subscribe(setSnapshot), []);

  // A changed text is a different utterance; the old clip must not be replayed for it.
  useEffect(() => {
    setClip(null);
    setError(null);
  }, [text, language, gender, speed]);

  const isThisClip = snapshot.clipId === clipId;
  const isPlaying = isThisClip && snapshot.state === "playing";

  const speak = useCallback(async () => {
    if (isPlaying) {
      playback.pause();
      return;
    }
    if (isThisClip && snapshot.state === "paused") {
      playback.resume();
      return;
    }

    setError(null);
    let response = clip;
    if (!response) {
      setLoading(true);
      try {
        response = await synthesize({ text, language, gender, speed });
        setClip(response);
      } catch (cause) {
        setError(
          cause instanceof SpeechRequestError
            ? cause.message
            : "The voice service could not be reached.",
        );
        return;
      } finally {
        setLoading(false);
      }
    }
    await playback.play(audioUrlFrom(response), clipId, { ownsUrl: true });
  }, [clip, clipId, gender, isPlaying, isThisClip, language, snapshot.state, speed, text]);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size={compact ? "sm" : "default"}
          variant="outline"
          onClick={() => void speak()}
          disabled={loading}
          aria-pressed={isPlaying}
          aria-label={isPlaying ? "Pause reading" : "Read this aloud"}
        >
          {loading ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : isPlaying ? (
            <Pause className="size-4" aria-hidden />
          ) : (
            <Volume2 className="size-4" aria-hidden />
          )}
          {compact ? null : <span>{isPlaying ? "Pause" : "Read aloud"}</span>}
        </Button>

        {isThisClip && snapshot.state !== "idle" ? (
          <Button
            type="button"
            size={compact ? "sm" : "default"}
            variant="ghost"
            onClick={() => playback.replay()}
            aria-label="Play again from the start"
          >
            <RotateCcw className="size-4" aria-hidden />
            {compact ? null : <span>Replay</span>}
          </Button>
        ) : null}

        {clip ? (
          <span className="text-xs text-[var(--color-muted-foreground)]">
            {voiceSourceLabel(clip)}
            {clip.degraded ? " (fallback)" : ""}
          </span>
        ) : null}
      </div>

      <p role="status" aria-live="polite" className="sr-only">
        {loading
          ? "Preparing audio"
          : isPlaying
            ? "Playing the spoken answer"
            : error
              ? error
              : ""}
      </p>

      {error ? (
        <p className="flex items-start gap-2 text-sm text-amber-600 dark:text-amber-400">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            {error} The written answer below is complete and unchanged.
          </span>
        </p>
      ) : null}

      {clip ? (
        <details className="text-sm text-[var(--color-muted-foreground)]">
          <summary className="cursor-pointer">What was spoken</summary>
          <p className="mt-1 leading-relaxed">{clip.spoken_text}</p>
        </details>
      ) : null}
    </div>
  );
}
