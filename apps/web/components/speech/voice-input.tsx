"use client";

/**
 * Press-and-hold microphone input (PLAN.md Phase 7.1, 7.2, 7.8).
 *
 * Press-and-hold rather than tap-to-toggle. On a moving boat with wet hands, a toggle that
 * silently stayed open is the failure mode — the user walks away believing they asked a
 * question, and either nothing was recorded or fifteen seconds of engine noise was. Holding
 * is unambiguous, and releasing is a deliberate act.
 *
 * Other decisions worth naming:
 *
 * - **Barge-in on press.** Opening the mic stops any playing audio (PLAN.md 7.8).
 * - **Every failure is recoverable and explained.** A denied permission, a silent
 *   recording and an unreachable service each say what happened and what to do, and the
 *   typed-input path stays available throughout. A voice interface that dead-ends when the
 *   mic fails is worse than no voice interface.
 * - **Low confidence asks rather than assumes.** Below the server's confidence floor the
 *   transcript is shown for confirmation before anything is done with it, because acting on
 *   a misheard place name produces a confident answer about the wrong stretch of sea.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, Square, Loader2, TriangleAlert, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  MAX_RECORDING_SECONDS,
  CaptureError,
  Recorder,
  isCaptureSupported,
} from "@/lib/speech/capture";
import {
  SpeechRequestError,
  transcribe,
  type LanguageCode,
  type TranscribeResponse,
} from "@/lib/speech/client";
import { playback } from "@/lib/speech/playback";

export interface VoiceInputProps {
  /** Called once a transcript is available and, if needed, confirmed by the user. */
  readonly onTranscript: (result: TranscribeResponse) => void;
  /** The user's stored language preference, used when a clip is too short to judge. */
  readonly preference?: LanguageCode;
  /** What this conversation has already settled on, so one "ok" cannot switch it. */
  readonly pinnedLanguage?: LanguageCode;
}

type Phase = "idle" | "recording" | "transcribing" | "confirming" | "error";

export function VoiceInput({ onTranscript, preference, pinnedLanguage }: VoiceInputProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<TranscribeResponse | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [supported, setSupported] = useState(true);
  const recorderRef = useRef<Recorder | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setSupported(isCaptureSupported());
    return () => {
      recorderRef.current?.cancel();
      if (tickRef.current) clearInterval(tickRef.current);
    };
  }, []);

  const stopTicking = useCallback(() => {
    if (tickRef.current) {
      clearInterval(tickRef.current);
      tickRef.current = null;
    }
  }, []);

  const start = useCallback(async () => {
    if (phase === "recording" || phase === "transcribing") return;

    // Barge-in: the user talking over the answer means they want the floor.
    playback.bargeIn();
    setError(null);
    setPending(null);
    setElapsed(0);

    const recorder = new Recorder();
    recorderRef.current = recorder;
    try {
      await recorder.start();
    } catch (cause) {
      setPhase("error");
      setError(
        cause instanceof CaptureError
          ? cause.message
          : "The microphone could not be opened.",
      );
      return;
    }

    setPhase("recording");
    const startedAt = Date.now();
    tickRef.current = setInterval(() => {
      const seconds = (Date.now() - startedAt) / 1000;
      setElapsed(seconds);
      if (seconds >= MAX_RECORDING_SECONDS) void finish();
    }, 100);
    // `finish` is stable enough for this interval; it is cleared on every exit path.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  const finish = useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    stopTicking();
    setPhase("transcribing");

    try {
      const audio = await recorder.stop();
      const result = await transcribe({
        audioBase64: audio.base64,
        durationSeconds: audio.durationSeconds,
        preference,
        pinnedLanguage,
      });

      if (result.needs_confirmation || hasAmbiguousPlace(result)) {
        setPending(result);
        setPhase("confirming");
        return;
      }
      setPhase("idle");
      onTranscript(result);
    } catch (cause) {
      setPhase("error");
      if (cause instanceof CaptureError) {
        setError(cause.message);
      } else if (cause instanceof SpeechRequestError) {
        setError(
          cause.status === 503
            ? "No speech recogniser is available right now. You can type your question instead."
            : cause.message,
        );
      } else {
        setError("Something went wrong. You can type your question instead.");
      }
    } finally {
      recorderRef.current = null;
    }
  }, [onTranscript, pinnedLanguage, preference, stopTicking]);

  const cancel = useCallback(() => {
    stopTicking();
    recorderRef.current?.cancel();
    recorderRef.current = null;
    setPhase("idle");
    setElapsed(0);
  }, [stopTicking]);

  if (!supported) {
    return (
      <p className="text-sm text-[var(--color-muted-foreground)]">
        This browser cannot record audio. Type your question instead.
      </p>
    );
  }

  const remaining = Math.max(0, MAX_RECORDING_SECONDS - elapsed);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <Button
          type="button"
          size="lg"
          variant={phase === "recording" ? "destructive" : "default"}
          onMouseDown={() => void start()}
          onMouseUp={() => void finish()}
          onMouseLeave={() => phase === "recording" && void finish()}
          onTouchStart={(event) => {
            event.preventDefault();
            void start();
          }}
          onTouchEnd={(event) => {
            event.preventDefault();
            void finish();
          }}
          disabled={phase === "transcribing"}
          aria-label={
            phase === "recording"
              ? "Recording. Release to send."
              : "Hold to ask a question by voice"
          }
        >
          {phase === "transcribing" ? (
            <Loader2 className="size-5 animate-spin" aria-hidden />
          ) : phase === "recording" ? (
            <Square className="size-5" aria-hidden />
          ) : (
            <Mic className="size-5" aria-hidden />
          )}
          <span>
            {phase === "recording"
              ? `Listening… ${remaining.toFixed(0)}s`
              : phase === "transcribing"
                ? "Transcribing…"
                : "Hold to speak"}
          </span>
        </Button>

        {phase === "recording" ? (
          <Button type="button" variant="ghost" onClick={cancel}>
            Cancel
          </Button>
        ) : null}
      </div>

      <p role="status" aria-live="polite" className="sr-only">
        {phase === "recording"
          ? "Recording"
          : phase === "transcribing"
            ? "Transcribing your question"
            : phase === "confirming"
              ? "Please confirm what was heard"
              : error ?? ""}
      </p>

      {phase === "recording" ? (
        <div
          className="h-1 w-full overflow-hidden rounded bg-[var(--color-border)]"
          role="presentation"
        >
          <div
            className="h-full bg-red-500 transition-[width] duration-100"
            style={{ width: `${(elapsed / MAX_RECORDING_SECONDS) * 100}%` }}
          />
        </div>
      ) : null}

      {phase === "confirming" && pending ? (
        <ConfirmTranscript
          result={pending}
          onConfirm={() => {
            setPhase("idle");
            setPending(null);
            onTranscript(pending);
          }}
          onRetry={() => {
            setPending(null);
            setPhase("idle");
          }}
        />
      ) : null}

      {phase === "error" && error ? (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
          <div className="flex flex-col gap-2">
            <p>{error}</p>
            <Button type="button" size="sm" variant="outline" onClick={() => setPhase("idle")}>
              Try again
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function hasAmbiguousPlace(result: TranscribeResponse): boolean {
  return result.places.some((place) => place.resolution === "ambiguous");
}

/**
 * Show the transcript and let the user accept or re-record.
 *
 * Shown rather than acted on whenever confidence is low or a place name was ambiguous.
 * One extra tap is a small price against answering confidently about the wrong place.
 */
function ConfirmTranscript({
  result,
  onConfirm,
  onRetry,
}: {
  readonly result: TranscribeResponse;
  readonly onConfirm: () => void;
  readonly onRetry: () => void;
}) {
  const ambiguous = result.places.filter((place) => place.resolution === "ambiguous");

  return (
    <div className="flex flex-col gap-3 rounded-md border border-[var(--color-border)] p-3">
      <div>
        <p className="text-xs uppercase tracking-wide text-[var(--color-muted-foreground)]">
          Did I hear you correctly?
        </p>
        <p className="mt-1 text-base leading-relaxed">{result.text}</p>
      </div>

      {ambiguous.length > 0 ? (
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {ambiguous
            .map(
              (place) =>
                `Did you mean ${place.candidates.join(" or ")} when you said “${place.query}”?`,
            )
            .join(" ")}
        </p>
      ) : null}

      <div className="flex gap-2">
        <Button type="button" size="sm" onClick={onConfirm}>
          <Check className="size-4" aria-hidden />
          <span>Yes, that&apos;s right</span>
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onRetry}>
          Say it again
        </Button>
      </div>
    </div>
  );
}
