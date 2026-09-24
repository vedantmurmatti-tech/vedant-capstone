import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/**
 * Reusable microphone-recording hook — captures audio into an in-memory
 * Blob via the browser's MediaRecorder API. This is voice INPUT only:
 * nothing here sends the Blob anywhere, transcribes it, or talks to any
 * backend. Speech-to-text and wiring the result into a conversation are
 * both explicitly out of scope for this step (see BUILD_LOG.md).
 *
 * Deliberately independent of any page or of Esmerelda's existing
 * speech-OUTPUT pipeline (`useEsmereldaSpeech.ts`) — that hook analyzes
 * audio already being played by the browser; this one captures audio
 * from the user's microphone. They don't share any state or audio graph.
 */

export type VoiceInputState = "idle" | "listening" | "stopped" | "permission-denied" | "unsupported";

// Same asymmetric-smoothing shape as useEsmereldaSpeech's speech-OUTPUT
// energy reading (see that file) — reused here for speech INPUT (the raw
// mic signal while recording) so both the "listening" and "speaking" Orb
// states are driven by the identical real-time-audio-reactive mechanism,
// just fed from a different source.
const MIC_ENERGY_ATTACK = 0.5;
const MIC_ENERGY_RELEASE = 0.15;
const MIC_ENERGY_GAIN = 3.5;
const MIC_ENERGY_IDLE_THRESHOLD = 0.004;

// Voice-activity/silence auto-stop, built on the exact same analyser loop
// as micEnergyRef above — no second audio graph. Two thresholds, not one,
// to avoid flicker right at a single boundary: recording only ever starts
// its silence countdown after real speech (>= VOICE) has actually been
// heard, and only counts time spent clearly quiet (< SILENCE) toward it —
// normal speech dipping briefly between words never triggers it, since it
// rarely holds continuously below SILENCE for the full window.
const VOICE_ACTIVITY_THRESHOLD = 0.09;
const SILENCE_ENERGY_THRESHOLD = 0.05;
const SILENCE_STOP_DELAY_MS = 1400;
const MAX_RECORDING_MS = 30_000;
// A brief noise transient — the mic stream's own start-up "pop", a key
// click, a door — can cross VOICE_ACTIVITY_THRESHOLD for a single frame
// or two without being real speech. Previously, one such frame was
// enough to latch hasDetectedSpeech permanently true; if the user's real
// first word then came more than SILENCE_STOP_DELAY_MS later (very
// plausible — reacting to the "Listening" indicator takes a moment),
// the recording auto-stopped on what was actually near-silence, which
// is exactly what makes Whisper hallucinate "Thank you." (a well-known
// artifact for silent/empty audio, not a transcription error). Requiring
// the energy to stay above threshold continuously for this long before
// it's trusted as "real speech has started" filters out that class of
// transient without meaningfully delaying detection of genuine speech,
// which lasts far longer than this.
const VOICE_ACTIVITY_SUSTAIN_MS = 150;
// A first attempt at the fix above used micEnergyRef itself (the same
// slow-release-smoothed value the Orb displays) for this sustain check —
// verified, by actually reproducing the bug, NOT to work: that value's
// own RELEASE smoothing (0.15/frame) takes roughly 250ms to decay back
// below VOICE_ACTIVITY_THRESHOLD after even a near-instantaneous loud
// transient, which is longer than the 150ms sustain window itself — so a
// single blip's smoothed *afterglow* alone could still satisfy "150ms
// continuously above threshold", defeating the whole point. Fixed with a
// second, independent, much-faster-decaying energy reading used only for
// this VAD decision (never for the Orb's own — intentionally slow,
// organic-looking — visual smoothing, which is untouched).
const VAD_ENERGY_ATTACK = 0.6;
const VAD_ENERGY_RELEASE = 0.6;

interface UseVoiceInputResult {
  state: VoiceInputState;
  /** The most recently completed recording, or null before any recording has finished. */
  audioBlob: Blob | null;
  /**
   * A live (non-React-state) 0..1 smoothed microphone loudness reading,
   * updated via requestAnimationFrame while `state === "listening"` —
   * the input-side counterpart to `useEsmereldaSpeech`'s `energyRef`, for
   * the Orb's existing energy-reactive rendering to also drive off real
   * mic volume instead of only TTS playback. Stays at 0 the rest of the
   * time, and stays at 0 forever if Web Audio can't be set up (recording
   * itself is entirely unaffected either way — see `startMicEnergyLoop`).
   */
  micEnergyRef: RefObject<number>;
  /**
   * An id unique to the most recent `start()` call — increments every
   * recording. Dev-only diagnostic aid (see BUILD_LOG.md's "thank you"
   * regression entry) for correlating "this Blob/transcript came from
   * *this* recording" across the console log lines below and
   * Dashboard.tsx's own `[VOICE]` pipeline logs.
   */
  recordingId: number;
  /** Requests mic access (if needed) and starts recording. Always starts a fresh recording — any previous Blob is discarded. */
  start: () => Promise<void>;
  /** Stops the active recording; `state` becomes "stopped" once the Blob is assembled. No-op if not currently listening. */
  stop: () => void;
  /** Returns to "idle" (or "unsupported") and discards any recorded Blob. */
  reset: () => void;
}

function isVoiceInputSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    typeof window !== "undefined" &&
    typeof window.MediaRecorder !== "undefined"
  );
}

export function useVoiceInput(): UseVoiceInputResult {
  const [state, setState] = useState<VoiceInputState>(() => (isVoiceInputSupported() ? "idle" : "unsupported"));
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [recordingId, setRecordingId] = useState(0);

  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mountedRef = useRef(true);
  const recordingIdRef = useRef(0); // the synchronous source of truth start()/onstop read; recordingId (state) mirrors it for consumers

  const micEnergyRef = useRef(0);
  const micAudioCtxRef = useRef<AudioContext | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  const micDataRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const micRafRef = useRef<number | null>(null);

  const stopMicEnergyLoop = useCallback(() => {
    if (micRafRef.current != null) {
      cancelAnimationFrame(micRafRef.current);
      micRafRef.current = null;
    }
    micEnergyRef.current = 0;
    micAnalyserRef.current = null;
    micDataRef.current = null;
    micAudioCtxRef.current?.close().catch(() => {});
    micAudioCtxRef.current = null;
  }, []);

  const releaseStream = useCallback(() => {
    stopMicEnergyLoop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, [stopMicEnergyLoop]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop(); // onstop (below) finishes the state transition and releases the mic
    }
  }, []);

  // Taps the live mic MediaStream with an AnalyserNode purely for reading
  // its loudness — deliberately never connects it to ctx.destination
  // (unlike useEsmereldaSpeech's TTS-playback analyser, which must, to
  // keep the audio audible). Connecting a mic input to the speakers would
  // cause real audio feedback/echo. If Web Audio can't be set up at all,
  // this just leaves micEnergyRef at 0 and — since silence detection below
  // is driven by that same reading — auto-stop never fires either, so the
  // caller falls back to manual stop only. MediaRecorder itself never goes
  // through this path, so recording is completely unaffected either way.
  const startMicEnergyLoop = useCallback((stream: MediaStream) => {
    try {
      const Ctor =
        window.AudioContext ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return;
      const ctx = new Ctor();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      micAudioCtxRef.current = ctx;
      micAnalyserRef.current = analyser;
      micDataRef.current = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));
    } catch {
      return; // listening still works — the Orb just falls back to its non-reactive "active" look
    }

    let hasDetectedSpeech = false;
    let voiceStartedAt: number | null = null; // when energy most recently crossed above VOICE_ACTIVITY_THRESHOLD, still unconfirmed
    let silenceStartedAt: number | null = null;
    let vadEnergy = 0; // fast-decaying, VAD-only — see VAD_ENERGY_ATTACK/RELEASE's comment above
    const recordingStartedAt = performance.now();
    let lastVadLog = 0;

    const tick = () => {
      const analyser = micAnalyserRef.current;
      const data = micDataRef.current;
      let target = 0;
      if (analyser && data) {
        analyser.getByteTimeDomainData(data);
        let sumSquares = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i] - 128) / 128;
          sumSquares += v * v;
        }
        target = Math.min(1, Math.sqrt(sumSquares / data.length) * MIC_ENERGY_GAIN);
      }
      const rate = target > micEnergyRef.current ? MIC_ENERGY_ATTACK : MIC_ENERGY_RELEASE;
      micEnergyRef.current += (target - micEnergyRef.current) * rate;
      if (micEnergyRef.current < MIC_ENERGY_IDLE_THRESHOLD) micEnergyRef.current = 0;

      // VAD-only reading — same raw `target`, independent fast-decaying
      // smoothing so a brief transient's "afterglow" clears in ~30-50ms,
      // well inside VOICE_ACTIVITY_SUSTAIN_MS, instead of the ~250ms the
      // Orb's own display smoothing takes. Never used for anything the
      // user sees.
      const vadRate = target > vadEnergy ? VAD_ENERGY_ATTACK : VAD_ENERGY_RELEASE;
      vadEnergy += (target - vadEnergy) * vadRate;

      const now = performance.now();

      if (import.meta.env.DEV && now - lastVadLog > 500) {
        lastVadLog = now;
        console.debug("[VOICE] VAD tick", {
          displayEnergy: Number(micEnergyRef.current.toFixed(3)),
          vadEnergy: Number(vadEnergy.toFixed(3)),
          hasDetectedSpeech,
          sustainingVoiceMs: voiceStartedAt != null ? Math.round(now - voiceStartedAt) : null,
          silenceMs: silenceStartedAt != null ? Math.round(now - silenceStartedAt) : null,
          elapsedMs: Math.round(now - recordingStartedAt),
        });
      }

      if (now - recordingStartedAt >= MAX_RECORDING_MS) {
        if (import.meta.env.DEV) console.debug("[VOICE] VAD stop reason: max recording duration reached");
        stop(); // hard cap — never record forever regardless of what the user is doing
        return;
      }

      if (vadEnergy >= VOICE_ACTIVITY_THRESHOLD) {
        if (!hasDetectedSpeech) {
          // Require the (fast-decaying) VAD energy to stay above threshold
          // continuously for VOICE_ACTIVITY_SUSTAIN_MS before trusting it
          // as real speech — see the constants' own comments for why a
          // single frame above threshold, or even the slower display
          // energy, isn't enough (both let a brief mic-start pop/click or
          // noise blip permanently latch as "speech").
          if (voiceStartedAt == null) voiceStartedAt = now;
          else if (now - voiceStartedAt >= VOICE_ACTIVITY_SUSTAIN_MS) {
            hasDetectedSpeech = true;
            if (import.meta.env.DEV) console.debug("[VOICE] sustained speech confirmed, silence countdown now armed");
          }
        }
        silenceStartedAt = null;
      } else {
        voiceStartedAt = null; // energy dropped before sustaining long enough — was a transient, not speech
        if (hasDetectedSpeech && vadEnergy < SILENCE_ENERGY_THRESHOLD) {
          if (silenceStartedAt == null) {
            silenceStartedAt = now;
          } else if (now - silenceStartedAt >= SILENCE_STOP_DELAY_MS) {
            if (import.meta.env.DEV) console.debug("[VOICE] VAD stop reason: sustained silence after confirmed speech");
            stop(); // sustained silence after real speech was heard — stop() -> onstop -> releaseStream tears this loop down
            return;
          }
        }
      }
      // Energy between the two thresholds (or below SILENCE before any
      // real speech was ever heard) intentionally does nothing — it's
      // either a normal brief dip mid-sentence or the user hasn't started
      // talking yet, neither of which should start or continue a countdown.

      micRafRef.current = requestAnimationFrame(tick);
    };
    micRafRef.current = requestAnimationFrame(tick);
  }, [stop]);

  const start = useCallback(async () => {
    if (!isVoiceInputSupported()) {
      setState("unsupported");
      return;
    }

    // Stop whatever's currently running first, so a stray call to start()
    // while already listening can't end up with two concurrent recorders.
    stop();
    releaseStream();

    chunksRef.current = [];
    setAudioBlob(null);
    const thisRecordingId = ++recordingIdRef.current;
    setRecordingId(thisRecordingId);

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      if (mountedRef.current) setState("permission-denied");
      return;
    }

    if (!mountedRef.current) {
      // Component unmounted while the permission prompt was pending —
      // release the mic immediately rather than leaving it captured.
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    streamRef.current = stream;
    startMicEnergyLoop(stream);
    const mimeType = window.MediaRecorder.isTypeSupported?.("audio/webm") ? "audio/webm" : undefined;
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);

    if (import.meta.env.DEV) {
      console.debug("[VOICE] recording start", { recordingId: thisRecordingId, mimeType: recorder.mimeType });
    }

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
      if (import.meta.env.DEV) {
        console.debug("[VOICE] recording stop", {
          recordingId: thisRecordingId,
          chunkCount: chunksRef.current.length,
          blobSize: blob.size,
          blobType: blob.type,
        });
      }
      releaseStream(); // also stops the mic-energy loop (see releaseStream)
      if (mountedRef.current) {
        setAudioBlob(blob);
        setState("stopped");
      }
    };

    recorderRef.current = recorder;
    recorder.start();
    setState("listening");
  }, [stop, releaseStream, startMicEnergyLoop]);

  const reset = useCallback(() => {
    setState(isVoiceInputSupported() ? "idle" : "unsupported");
    setAudioBlob(null);
    chunksRef.current = [];
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        recorder.stop();
      }
      releaseStream();
    };
  }, [releaseStream]);

  return { state, audioBlob, micEnergyRef, recordingId, start, stop, reset };
}
