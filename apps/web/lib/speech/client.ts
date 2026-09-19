/**
 * Typed client for the speech endpoints (PLAN.md Phase 7.2, 7.6).
 *
 * Thin on purpose. The interesting decisions — which provider spoke, which language to
 * reply in, whether a number survived normalization — are all made server-side, because
 * they must be identical for every client and must be recorded in provenance. This file
 * moves bytes and surfaces the results the UI needs to be honest about degradation.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type LanguageCode =
  | "en"
  | "ta"
  | "hi"
  | "te"
  | "ml"
  | "or"
  | "bn"
  | "gu"
  | "mr"
  | "kn";

/** Where a result came from in the documented hierarchy (PLAN.md §1.3). */
export type ProviderTier = "primary" | "self_hosted" | "cache" | "client";

export interface ProviderAttempt {
  readonly provider: string;
  readonly tier: ProviderTier;
  readonly outcome: string;
  readonly detail: string | null;
}

export interface ResolvedPlace {
  readonly query: string;
  readonly resolution: "exact" | "fuzzy" | "ambiguous" | "unknown";
  readonly place_id: string | null;
  readonly name: string | null;
  readonly lat: number | null;
  readonly lon: number | null;
  readonly candidates: readonly string[];
}

export interface TranscribeResponse {
  readonly text: string;
  readonly lang: LanguageCode;
  readonly confidence: number;
  readonly alternatives: readonly string[];
  readonly detection_method: string;
  readonly code_mixed: boolean;
  readonly needs_confirmation: boolean;
  readonly reply_language: LanguageCode;
  readonly language_changed: boolean;
  readonly places: readonly ResolvedPlace[];
  readonly provider: string;
  readonly tier: ProviderTier;
  readonly attempts: readonly ProviderAttempt[];
}

export interface SynthesizeResponse {
  readonly audio_base64: string;
  readonly content_type: string;
  readonly spoken_text: string;
  readonly language: LanguageCode;
  readonly voice: string;
  readonly speed: number;
  readonly cache_key: string;
  readonly cache_hit: boolean;
  readonly provider: string;
  readonly tier: ProviderTier;
  readonly degraded: boolean;
  readonly attempts: readonly ProviderAttempt[];
}

export interface SpeechCapabilities {
  readonly languages: readonly {
    readonly code: LanguageCode;
    readonly english_name: string;
    readonly native_name: string;
    readonly rehearsed: boolean;
    readonly voices: readonly { readonly voice_id: string; readonly gender: string }[];
  }[];
  readonly asr_providers: readonly string[];
  readonly tts_providers: readonly string[];
  readonly bhashini_configured: boolean;
  readonly self_hosted_models_available: boolean;
  readonly cached_clips: number;
}

/**
 * A speech request that failed with a reason worth showing.
 *
 * `attempts` is carried through rather than flattened into a message: "no voice is
 * available" tells a user nothing, while "the cached voice has this phrase, the live one
 * is out of quota" tells an operator exactly what to do.
 */
export class SpeechRequestError extends Error {
  readonly status: number;
  readonly attempts: readonly ProviderAttempt[];

  constructor(status: number, message: string, attempts: readonly ProviderAttempt[] = []) {
    super(message);
    this.name = "SpeechRequestError";
    this.status = status;
    this.attempts = attempts;
  }

  /** Whether retrying might help, as opposed to the request itself being wrong. */
  get isTransient(): boolean {
    return this.status >= 500;
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new SpeechRequestError(0, "ORCA is unreachable. Check your connection.");
  }

  if (!response.ok) {
    const detail = await readDetail(response);
    throw new SpeechRequestError(response.status, detail.message, detail.attempts);
  }
  return (await response.json()) as T;
}

async function readDetail(
  response: Response,
): Promise<{ message: string; attempts: readonly ProviderAttempt[] }> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail;
    if (typeof detail === "string") return { message: detail, attempts: [] };
    if (detail && typeof detail === "object") {
      const record = detail as { message?: string; attempts?: ProviderAttempt[] };
      return {
        message: record.message ?? `Request failed (${response.status})`,
        attempts: record.attempts ?? [],
      };
    }
  } catch {
    // Fall through to the status-only message.
  }
  return { message: `Request failed (${response.status})`, attempts: [] };
}

export interface TranscribeOptions {
  readonly audioBase64: string;
  readonly durationSeconds?: number;
  readonly language?: LanguageCode;
  readonly preference?: LanguageCode;
  readonly pinnedLanguage?: LanguageCode;
  /**
   * The Web Speech API's own guess, if the browser produced one.
   *
   * Sent every time it exists, not only when the network looks bad: it is the last tier of
   * the ASR chain, and the server decides whether it is needed.
   */
  readonly browserTranscript?: string;
}

export async function transcribe(options: TranscribeOptions): Promise<TranscribeResponse> {
  return post<TranscribeResponse>("/api/v1/speech/transcribe", {
    audio_base64: options.audioBase64,
    duration_seconds: options.durationSeconds,
    language: options.language,
    preference: options.preference,
    pinned_language: options.pinnedLanguage,
    browser_transcript: options.browserTranscript,
  });
}

export async function synthesize(options: {
  readonly text: string;
  readonly language: LanguageCode;
  readonly gender?: "female" | "male";
  readonly speed?: number;
}): Promise<SynthesizeResponse> {
  return post<SynthesizeResponse>("/api/v1/speech/synthesize", {
    text: options.text,
    language: options.language,
    gender: options.gender ?? "female",
    speed: options.speed ?? 1.0,
  });
}

export async function fetchCapabilities(): Promise<SpeechCapabilities> {
  const response = await fetch(`${API_BASE_URL}/api/v1/speech/capabilities`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new SpeechRequestError(response.status, "Could not read speech capabilities.");
  }
  return (await response.json()) as SpeechCapabilities;
}

/** Turn a base64 clip into a playable object URL. */
export function audioUrlFrom(response: SynthesizeResponse): string {
  const binary = atob(response.audio_base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: response.content_type }));
}

/**
 * How to describe a clip's origin in the UI.
 *
 * Audio carries no visible provenance, so a third-fallback clip sounds exactly as
 * authoritative as a first-choice one. Saying which voice spoke is the same honesty ORCA
 * applies to stale data everywhere else.
 */
export function voiceSourceLabel(response: SynthesizeResponse): string {
  if (response.cache_hit) return "Saved voice";
  switch (response.tier) {
    case "primary":
      return "Live voice";
    case "self_hosted":
      return "Offline voice";
    case "cache":
      return "Saved voice";
    default:
      return "Device voice";
  }
}
