/**
 * Shared playback control with barge-in (PLAN.md Phase 7.8).
 *
 * A module-level singleton, which is unusual enough to justify: there is exactly one pair
 * of ears. Two clips playing at once is never useful, and "read aloud" buttons on several
 * cards would otherwise happily overlap into noise. Routing every clip through one
 * controller makes "starting a clip stops the previous one" structural rather than
 * something each component has to remember.
 *
 * **Barge-in** is the same mechanism: when the mic opens, audio stops immediately. Someone
 * interrupting the assistant means they want to speak, and talking over them is both rude
 * and, on a boat with an engine running, a genuine intelligibility problem.
 */

export type PlaybackState = "idle" | "playing" | "paused";

export interface PlaybackSnapshot {
  readonly state: PlaybackState;
  /** Identifies which clip is playing, so only the right card shows a pause button. */
  readonly clipId: string | null;
  readonly positionSeconds: number;
  readonly durationSeconds: number;
}

type Listener = (snapshot: PlaybackSnapshot) => void;

class PlaybackController {
  private audio: HTMLAudioElement | null = null;
  private clipId: string | null = null;
  private objectUrl: string | null = null;
  private listeners = new Set<Listener>();

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => {
      this.listeners.delete(listener);
    };
  }

  snapshot(): PlaybackSnapshot {
    const audio = this.audio;
    return {
      state: !audio || audio.ended ? "idle" : audio.paused ? "paused" : "playing",
      clipId: this.clipId,
      positionSeconds: audio?.currentTime ?? 0,
      durationSeconds: Number.isFinite(audio?.duration ?? NaN) ? (audio?.duration ?? 0) : 0,
    };
  }

  /** Play a clip, replacing whatever was playing. */
  async play(url: string, clipId: string, options?: { ownsUrl?: boolean }): Promise<void> {
    this.stop();

    const audio = new Audio(url);
    audio.preload = "auto";
    this.audio = audio;
    this.clipId = clipId;
    this.objectUrl = options?.ownsUrl ? url : null;

    audio.ontimeupdate = () => this.emit();
    audio.onended = () => {
      this.emit();
      this.release();
    };
    audio.onpause = () => this.emit();
    audio.onplay = () => this.emit();

    try {
      await audio.play();
    } catch {
      // Autoplay policy, or the tab lost focus mid-request. Not an error worth showing:
      // the transcript is on screen either way, and the user can press play.
      this.emit();
    }
  }

  pause(): void {
    this.audio?.pause();
    this.emit();
  }

  resume(): void {
    void this.audio?.play();
    this.emit();
  }

  /** Start the current clip again from the beginning. */
  replay(): void {
    if (!this.audio) return;
    this.audio.currentTime = 0;
    void this.audio.play();
    this.emit();
  }

  stop(): void {
    if (this.audio) {
      this.audio.pause();
      this.audio.ontimeupdate = null;
      this.audio.onended = null;
      this.audio.onpause = null;
      this.audio.onplay = null;
    }
    this.release();
    this.emit();
  }

  /** Stop audio because the user started speaking (PLAN.md 7.8). */
  bargeIn(): void {
    this.stop();
  }

  private release(): void {
    if (this.objectUrl) {
      URL.revokeObjectURL(this.objectUrl);
      this.objectUrl = null;
    }
    this.audio = null;
    this.clipId = null;
  }

  private emit(): void {
    const snapshot = this.snapshot();
    this.listeners.forEach((listener) => listener(snapshot));
  }
}

export const playback = new PlaybackController();
