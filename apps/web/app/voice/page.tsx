"use client";

/**
 * Voice interaction surface (PLAN.md Phase 7.1, 7.8, 7.10).
 *
 * The screen the Tamil voice demo runs on. It exists to exercise the whole speech path
 * end to end — capture, transcribe, language selection, place resolution, read-aloud — and
 * to show the things ORCA insists on showing: which voice spoke, what it actually said, and
 * where the language came from.
 *
 * The answer cards here carry **placeholder wording**, not kernel output. Wiring the
 * orchestrator's rendered templates into this page is Phase 8 work; pretending otherwise
 * on a page a jury might see would be the sort of claim this project has avoided
 * throughout. The read-aloud path below is real — it speaks whatever text it is given
 * through the same normalizer, cache and fallback chain as production.
 */

import { useCallback, useEffect, useState } from "react";
import { VoiceInput } from "@/components/speech/voice-input";
import { ReadAloud } from "@/components/speech/read-aloud";
import {
  fetchCapabilities,
  type LanguageCode,
  type SpeechCapabilities,
  type TranscribeResponse,
} from "@/lib/speech/client";
import { Button } from "@/components/ui/button";

/**
 * Sample sentences, one per rehearsed language, shaped exactly like real rendered
 * templates — the same slots, units and figures — so the read-aloud path is exercised on
 * text with the properties that matter rather than on a lorem-ipsum string.
 */
const SAMPLE_ANSWER: Record<string, string> = {
  en: "Conditions are safe for your FRP vallam. Wave height 1.1 m, wind 6.0 m/s. Safety score 78 out of 100. Data is 2.0 h old.",
  ta: "உங்கள் FRP vallam படகுக்கு கடல் நிலை பாதுகாப்பானது. அலை உயரம் 1.1 m, காற்று 6.0 m/s. பாதுகாப்பு மதிப்பெண் 100 இல் 78. தரவின் வயது 2.0 h.",
  hi: "आपकी FRP vallam नाव के लिए स्थिति सुरक्षित है। लहर की ऊंचाई 1.1 m, हवा 6.0 m/s। सुरक्षा स्कोर 100 में से 78। डेटा 2.0 h पुराना है।",
};

export default function VoicePage() {
  const [capabilities, setCapabilities] = useState<SpeechCapabilities | null>(null);
  const [capabilitiesError, setCapabilitiesError] = useState<string | null>(null);
  const [preference, setPreference] = useState<LanguageCode>("ta");
  const [pinned, setPinned] = useState<LanguageCode | undefined>(undefined);
  const [turns, setTurns] = useState<TranscribeResponse[]>([]);

  useEffect(() => {
    fetchCapabilities()
      .then(setCapabilities)
      .catch(() => setCapabilitiesError("Speech capabilities could not be read."));
  }, []);

  const handleTranscript = useCallback((result: TranscribeResponse) => {
    setTurns((previous) => [result, ...previous]);
    setPinned(result.reply_language);
  }, []);

  const answerLanguage = (pinned ?? preference) as LanguageCode;
  const answer = SAMPLE_ANSWER[answerLanguage] ?? SAMPLE_ANSWER.en;

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-8 px-4 py-12">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Ask ORCA</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          Hold the button and ask in Tamil, Hindi or English. ORCA replies in the language
          you asked in.
        </p>
      </header>

      <section aria-labelledby="language-heading" className="flex flex-col gap-3">
        <h2 id="language-heading" className="text-sm font-medium">
          Preferred language
        </h2>
        <div className="flex flex-wrap gap-2">
          {(capabilities?.languages ?? []).map((language) => (
            <Button
              key={language.code}
              type="button"
              size="sm"
              variant={preference === language.code ? "default" : "outline"}
              onClick={() => {
                setPreference(language.code);
                setPinned(undefined);
              }}
              aria-pressed={preference === language.code}
            >
              <span>{language.native_name}</span>
              {!language.rehearsed ? (
                <span className="text-xs opacity-70">(beta)</span>
              ) : null}
            </Button>
          ))}
        </div>
        <p className="text-xs text-[var(--color-muted-foreground)]">
          Used only when a recording is too short to tell. Tamil, Hindi and English are
          fully tested; the rest are enabled and smoke-tested.
        </p>
      </section>

      <section aria-labelledby="ask-heading" className="flex flex-col gap-3">
        <h2 id="ask-heading" className="text-sm font-medium">
          Ask
        </h2>
        <VoiceInput
          onTranscript={handleTranscript}
          preference={preference}
          pinnedLanguage={pinned}
        />
      </section>

      <section
        aria-labelledby="answer-heading"
        className="flex flex-col gap-3 rounded-lg border border-[var(--color-border)] p-5"
      >
        <div className="flex items-center justify-between">
          <h2 id="answer-heading" className="text-lg font-medium">
            Answer
          </h2>
          <span className="rounded bg-[var(--color-muted)] px-2 py-0.5 text-xs">
            Sample text — not a live advisory
          </span>
        </div>
        <p className="leading-relaxed">{answer}</p>
        <ReadAloud text={answer} language={answerLanguage} clipId="sample-answer" />
      </section>

      {turns.length > 0 ? (
        <section aria-labelledby="heard-heading" className="flex flex-col gap-3">
          <h2 id="heard-heading" className="text-sm font-medium">
            What ORCA heard
          </h2>
          <ul className="flex flex-col gap-3">
            {turns.map((turn, index) => (
              <li
                key={`${turn.text}-${index}`}
                className="rounded-md border border-[var(--color-border)] p-3 text-sm"
              >
                <p className="leading-relaxed">{turn.text}</p>
                <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-[var(--color-muted-foreground)]">
                  <dt className="font-medium">Language</dt>
                  <dd>
                    {turn.lang} ({turn.detection_method}
                    {turn.code_mixed ? ", code-mixed" : ""})
                  </dd>
                  <dt className="font-medium">Heard by</dt>
                  <dd>
                    {turn.provider} ({turn.tier})
                  </dd>
                  <dt className="font-medium">Confidence</dt>
                  <dd>{(turn.confidence * 100).toFixed(0)}%</dd>
                  {turn.places.length > 0 ? (
                    <>
                      <dt className="font-medium">Places</dt>
                      <dd>
                        {turn.places
                          .map((place) => place.name ?? `${place.query} (unclear)`)
                          .join(", ")}
                      </dd>
                    </>
                  ) : null}
                </dl>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section
        aria-labelledby="status-heading"
        className="flex flex-col gap-2 rounded-lg border border-[var(--color-border)] p-4 text-sm"
      >
        <h2 id="status-heading" className="font-medium">
          Speech services
        </h2>
        {capabilitiesError ? (
          <p className="text-amber-600 dark:text-amber-400">{capabilitiesError}</p>
        ) : capabilities ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-[var(--color-muted-foreground)]">
            <dt>Bhashini</dt>
            <dd>{capabilities.bhashini_configured ? "configured" : "not configured"}</dd>
            <dt>Self-hosted models</dt>
            <dd>
              {capabilities.self_hosted_models_available ? "loaded" : "not installed"}
            </dd>
            <dt>Cached clips</dt>
            <dd>{capabilities.cached_clips}</dd>
            <dt>Voice chain</dt>
            <dd>{capabilities.tts_providers.join(" → ")}</dd>
          </dl>
        ) : (
          <p className="text-[var(--color-muted-foreground)]">Checking…</p>
        )}
      </section>
    </main>
  );
}
