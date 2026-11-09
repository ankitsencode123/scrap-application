/**
 * Modern browser Web Audio API synthesized notification sound.
 * Generates an instant, gentle melodic arrival chime
 * with zero external assets, zero bandwidth, and zero network latency.
 */
let audioCtx: AudioContext | null = null;

export function isChatSoundMuted(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem("scrappe_chat_sound_muted") === "true";
  } catch {
    return false;
  }
}

export function setChatSoundMuted(muted: boolean): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem("scrappe_chat_sound_muted", muted ? "true" : "false");
  } catch {
    // Ignore localStorage availability errors
  }
}

export function playChatChime(): void {
  if (typeof window === "undefined") return;
  if (isChatSoundMuted()) return;

  try {
    const AudioContextClass =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextClass) return;

    if (!audioCtx || audioCtx.state === "closed") {
      audioCtx = new AudioContextClass();
    }
    if (audioCtx.state === "suspended") {
      void audioCtx.resume();
    }

    const ctx = audioCtx;
    const now = ctx.currentTime;

    // Pleasant two-tone chord: E5 (659.25Hz) followed by A5 (880Hz)
    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gainNode = ctx.createGain();

    osc1.type = "sine";
    osc2.type = "sine";

    osc1.frequency.setValueAtTime(659.25, now);
    osc2.frequency.setValueAtTime(880.0, now + 0.08);

    gainNode.gain.setValueAtTime(0, now);
    gainNode.gain.linearRampToValueAtTime(0.12, now + 0.02);
    gainNode.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);

    osc1.connect(gainNode);
    osc2.connect(gainNode);
    gainNode.connect(ctx.destination);

    osc1.start(now);
    osc1.stop(now + 0.12);
    osc2.start(now + 0.08);
    osc2.stop(now + 0.35);
  } catch {
    // Autoplay restrictions or unavailable audio device - ignore silently
  }
}
