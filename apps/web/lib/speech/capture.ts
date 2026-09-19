/**
 * Microphone capture for the PWA (PLAN.md Phase 7.1).
 *
 * Target device: a low-end Android phone on Chrome, held by someone standing on a boat.
 * That constrains nearly every choice here.
 *
 * - **16 kHz mono PCM16 WAV.** What IndicConformer and IndicWhisper expect, and roughly a
 *   tenth the bytes of the 48 kHz stereo the browser hands over. On a 2G link that
 *   difference is the whole request.
 * - **Resampling in an OfflineAudioContext.** `MediaRecorder` produces WebM/Opus at the
 *   device's own rate, which varies by handset; decoding and rendering to a fixed rate
 *   makes the upload identical from any phone.
 * - **Energy-based VAD trimming.** Leading and trailing silence is most of a short
 *   recording. Trimming is not a nicety — it removes the fumbling before the question and
 *   the pause before the thumb lifts, both of which ASR will otherwise try to interpret.
 * - **A hard 15 s cap, enforced by a timer rather than trusted to the user.**
 *
 * No transcription happens here. This module produces bytes; the server decides what they
 * say, because the fallback hierarchy and its provenance live there.
 */

/** A hard cap on utterance length (PLAN.md 7.1). */
export const MAX_RECORDING_SECONDS = 15;

/** What ASR providers expect, and what the WAV is always written at. */
export const TARGET_SAMPLE_RATE = 16_000;

/**
 * RMS below which a frame counts as silence.
 *
 * Deliberately low. A boat is loud — engine, wind, water — and an aggressive threshold
 * clips the first syllable of a question, which is exactly the word that carries the
 * intent. Trimming too little costs a few kilobytes; trimming too much costs the meaning.
 */
const SILENCE_RMS_THRESHOLD = 0.012;

/** Frame size for the energy scan: 20 ms, the usual granularity for this. */
const VAD_FRAME_SAMPLES = TARGET_SAMPLE_RATE / 50;

/** Padding kept either side of detected speech, so trimming never clips a consonant. */
const VAD_PADDING_FRAMES = 6;

export type CaptureErrorKind =
  | "unsupported"
  | "permission-denied"
  | "no-microphone"
  | "no-speech"
  | "capture-failed";

/**
 * A capture failure the UI can act on.
 *
 * The kinds are distinct because the right response differs completely: a denied
 * permission needs an explanation and a settings link, no speech needs "try again, closer
 * to the mic", and an unsupported browser needs the typed-input path offered instead.
 */
export class CaptureError extends Error {
  readonly kind: CaptureErrorKind;

  constructor(kind: CaptureErrorKind, message: string) {
    super(message);
    this.name = "CaptureError";
    this.kind = kind;
  }
}

export interface CapturedAudio {
  /** 16 kHz mono PCM16 WAV. */
  readonly wav: Blob;
  /** Base64 of the same bytes, ready for the JSON request body. */
  readonly base64: string;
  readonly durationSeconds: number;
  readonly sampleRateHz: number;
  /** Seconds removed from the ends by the VAD trim, for the "nothing was heard" case. */
  readonly trimmedSeconds: number;
}

export function isCaptureSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof window.MediaRecorder !== "undefined" &&
    typeof window.AudioContext !== "undefined"
  );
}

/** Whether the mic is already granted, so the UI can skip the explanation. */
export async function microphonePermissionState(): Promise<PermissionState | "unknown"> {
  if (typeof navigator === "undefined" || !navigator.permissions?.query) return "unknown";
  try {
    const status = await navigator.permissions.query({
      name: "microphone" as PermissionName,
    });
    return status.state;
  } catch {
    // Firefox and older Chrome do not expose the microphone permission this way. Not
    // knowing is fine; the UI just asks.
    return "unknown";
  }
}

/**
 * A single recording session.
 *
 * Kept as an object rather than a one-shot promise because the UI needs to stop early —
 * the common interaction is press-and-hold, and the user's thumb, not a timer, decides
 * when the question ended.
 */
export class Recorder {
  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private chunks: Blob[] = [];
  private stopTimer: ReturnType<typeof setTimeout> | null = null;
  private finished: Promise<Blob> | null = null;

  get recording(): boolean {
    return this.recorder?.state === "recording";
  }

  async start(): Promise<void> {
    if (!isCaptureSupported()) {
      throw new CaptureError(
        "unsupported",
        "This browser cannot record audio. You can type your question instead.",
      );
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (cause) {
      const name = cause instanceof DOMException ? cause.name : "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        throw new CaptureError(
          "permission-denied",
          "Microphone access was blocked. Allow it in your browser settings to ask by voice.",
        );
      }
      if (name === "NotFoundError" || name === "DevicesNotFoundError") {
        throw new CaptureError("no-microphone", "No microphone was found on this device.");
      }
      throw new CaptureError("capture-failed", "The microphone could not be opened.");
    }

    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream, { mimeType: pickMimeType() });
    this.recorder.ondataavailable = (event) => {
      if (event.data.size > 0) this.chunks.push(event.data);
    };

    this.finished = new Promise<Blob>((resolve) => {
      this.recorder!.onstop = () => {
        resolve(new Blob(this.chunks, { type: this.recorder?.mimeType ?? "audio/webm" }));
      };
    });

    this.recorder.start();
    // The cap is enforced here rather than trusted to the UI: an unbounded upload is a
    // cost and a denial-of-service surface, and the server rejects long clips anyway.
    this.stopTimer = setTimeout(() => void this.stop(), MAX_RECORDING_SECONDS * 1000);
  }

  /** Stop, decode, resample, trim and encode. Releases the mic before returning. */
  async stop(): Promise<CapturedAudio> {
    if (!this.recorder || !this.finished) {
      throw new CaptureError("capture-failed", "Nothing was being recorded.");
    }
    if (this.stopTimer) {
      clearTimeout(this.stopTimer);
      this.stopTimer = null;
    }
    if (this.recorder.state !== "inactive") this.recorder.stop();

    const raw = await this.finished;
    this.release();
    return encodeForAsr(raw);
  }

  /** Abandon the recording — used when the user cancels or navigates away. */
  cancel(): void {
    if (this.stopTimer) {
      clearTimeout(this.stopTimer);
      this.stopTimer = null;
    }
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    this.release();
  }

  private release(): void {
    // Leaving a track live keeps the recording indicator on, which reads to a user as the
    // app still listening. It also drains a phone battery.
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.recorder = null;
  }
}

function pickMimeType(): string {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

/** Decode whatever the browser recorded into a trimmed 16 kHz mono WAV. */
export async function encodeForAsr(raw: Blob): Promise<CapturedAudio> {
  const buffer = await raw.arrayBuffer();
  const context = new AudioContext();
  let decoded: AudioBuffer;
  try {
    decoded = await context.decodeAudioData(buffer.slice(0));
  } finally {
    void context.close();
  }

  const samples = await resampleToMono(decoded, TARGET_SAMPLE_RATE);
  const { trimmed, removedSamples } = trimSilence(samples);

  if (trimmed.length === 0) {
    throw new CaptureError(
      "no-speech",
      "Nothing was heard. Hold the button and speak close to the microphone.",
    );
  }

  const wav = encodeWav(trimmed, TARGET_SAMPLE_RATE);
  return {
    wav,
    base64: await blobToBase64(wav),
    durationSeconds: trimmed.length / TARGET_SAMPLE_RATE,
    sampleRateHz: TARGET_SAMPLE_RATE,
    trimmedSeconds: removedSamples / TARGET_SAMPLE_RATE,
  };
}

/** Mix to mono and resample, so every handset uploads the same shape. */
async function resampleToMono(source: AudioBuffer, rate: number): Promise<Float32Array> {
  const frames = Math.ceil((source.duration * rate));
  const offline = new OfflineAudioContext(1, frames, rate);
  const node = offline.createBufferSource();
  node.buffer = source;
  node.connect(offline.destination);
  node.start();
  const rendered = await offline.startRendering();
  return rendered.getChannelData(0);
}

/**
 * Drop leading and trailing silence, keeping a short pad either side.
 *
 * Only the ends are trimmed. Cutting silence from the middle would splice two halves of a
 * sentence together and change what was said — a pause between words is information.
 */
export function trimSilence(samples: Float32Array): {
  trimmed: Float32Array;
  removedSamples: number;
} {
  const frameCount = Math.floor(samples.length / VAD_FRAME_SAMPLES);
  let firstVoiced = -1;
  let lastVoiced = -1;

  for (let frame = 0; frame < frameCount; frame += 1) {
    const start = frame * VAD_FRAME_SAMPLES;
    let sumSquares = 0;
    for (let i = start; i < start + VAD_FRAME_SAMPLES; i += 1) {
      sumSquares += samples[i] * samples[i];
    }
    const rms = Math.sqrt(sumSquares / VAD_FRAME_SAMPLES);
    if (rms >= SILENCE_RMS_THRESHOLD) {
      if (firstVoiced === -1) firstVoiced = frame;
      lastVoiced = frame;
    }
  }

  if (firstVoiced === -1) {
    return { trimmed: new Float32Array(0), removedSamples: samples.length };
  }

  const startFrame = Math.max(0, firstVoiced - VAD_PADDING_FRAMES);
  const endFrame = Math.min(frameCount, lastVoiced + VAD_PADDING_FRAMES + 1);
  const start = startFrame * VAD_FRAME_SAMPLES;
  const end = Math.min(samples.length, endFrame * VAD_FRAME_SAMPLES);

  return {
    trimmed: samples.slice(start, end),
    removedSamples: samples.length - (end - start),
  };
}

/** Write a minimal 16-bit PCM WAV. */
export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const bytesPerSample = 2;
  const buffer = new ArrayBuffer(44 + samples.length * bytesPerSample);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * bytesPerSample, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true); // byte rate
  view.setUint16(32, bytesPerSample, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * bytesPerSample, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    // Clamp before scaling: a sample beyond ±1 would wrap to the opposite extreme and be
    // heard as a click.
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += bytesPerSample;
  }

  return new Blob([buffer], { type: "audio/wav" });
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
}

export async function blobToBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  // Chunked so a long clip does not blow the argument limit of String.fromCharCode.
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}
