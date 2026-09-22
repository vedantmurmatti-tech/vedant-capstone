import { useCallback, useEffect, useRef, useState } from "react";
import { synthesizeSpeech } from "@/lib/api";
import { stripMarkdownForSpeech } from "@/lib/utils";

// Exponential smoothing rates for the energy reading below — asymmetric on
// purpose: energy should rise quickly on a loud syllable (attack) but fall
// gently through a short pause (release), which is what actually produces
// an organic "settle" instead of a snap to silence.
const ENERGY_ATTACK = 0.45;
const ENERGY_RELEASE = 0.12;
const ENERGY_GAIN = 3.2; // raw time-domain RMS is small; scales it to use more of the 0..1 range
const ENERGY_IDLE_THRESHOLD = 0.004;

type AudioContextCtor = typeof AudioContext;

/**
 * Speaks Esmerelda's chat replies aloud, one at a time, through the
 * existing POST /api/speech endpoint (backend/api/tts.py — no separate
 * TTS implementation here). Only ever called with a final assistant
 * reply's text — never user messages, loading text, errors, or raw
 * tool/debug output, since those never pass through `speak()`.
 *
 * `speak()` always stops whatever is currently playing (or still being
 * fetched) first, so at most one response is ever audible, and a
 * superseded in-flight request never starts playing after a newer one
 * has already taken over. A TTS failure is swallowed here — it never
 * throws back into the caller, so a voice outage can't break the chat
 * itself.
 *
 * Also exposes `energyRef` — a continuously-updated (via requestAnimationFrame,
 * not React state, so reading it never triggers a re-render) smoothed 0..1
 * loudness reading of the *same* `<audio>` element actually playing,
 * tapped through a Web Audio `AnalyserNode`. It is the single source of
 * truth AiCore's orb reads to react to real speech. If Web Audio can't be
 * set up at all (unsupported browser, blocked API, anything), `energyRef`
 * simply stays at 0 forever and playback is completely unaffected —
 * `ensureWebAudioGraph` never touches `audio.play()`/`audio.src`.
 */
export function useEsmereldaSpeech() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const requestIdRef = useRef(0);
  const [isSpeaking, setIsSpeaking] = useState(false);

  const energyRef = useRef(0);
  const webAudioAttemptedRef = useRef(false);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const analyserDataRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const energyRafRef = useRef<number | null>(null);

  // Taps the given <audio> element's output into an AnalyserNode without
  // changing what the listener hears. `createMediaElementSource` can only
  // be called once per element, ever — guarded so this only ever runs
  // once for the one <audio> element `speak()` reuses across calls.
  const ensureWebAudioGraph = useCallback((audioEl: HTMLAudioElement) => {
    if (webAudioAttemptedRef.current) return;
    webAudioAttemptedRef.current = true;

    try {
      const Ctor: AudioContextCtor | undefined =
        window.AudioContext ??
        (window as typeof window & { webkitAudioContext?: AudioContextCtor }).webkitAudioContext;
      if (!Ctor) return;

      const ctx = new Ctor();
      const source = ctx.createMediaElementSource(audioEl);

      try {
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        analyser.connect(ctx.destination);
        audioCtxRef.current = ctx;
        analyserRef.current = analyser;
        analyserDataRef.current = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));
      } catch {
        // The element's output was already captured by the graph above —
        // reconnect straight to the speakers so audio isn't silently
        // lost, just without any energy analysis.
        source.connect(ctx.destination);
      }
    } catch (err) {
      // No AudioContext, or the browser refused it for some reason.
      // energyRef simply stays 0 — playback itself never went through
      // this path, so it's already working normally.
      console.warn("Esmerelda: Web Audio setup failed; the orb won't react to speech.", err);
    }
  }, []);

  const stepEnergy = useCallback(() => {
    const audio = audioRef.current;
    const analyser = analyserRef.current;
    const data = analyserDataRef.current;
    const stillPlaying = !!audio && !audio.paused && !audio.ended;

    let target = 0;
    if (stillPlaying && analyser && data) {
      analyser.getByteTimeDomainData(data);
      let sumSquares = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sumSquares += v * v;
      }
      const rms = Math.sqrt(sumSquares / data.length);
      target = Math.min(1, rms * ENERGY_GAIN);
    }

    const rate = target > energyRef.current ? ENERGY_ATTACK : ENERGY_RELEASE;
    energyRef.current += (target - energyRef.current) * rate;
    if (energyRef.current < ENERGY_IDLE_THRESHOLD) energyRef.current = 0;

    if (stillPlaying || energyRef.current > 0) {
      energyRafRef.current = requestAnimationFrame(stepEnergy);
    } else {
      energyRafRef.current = null;
    }
  }, []);

  const startEnergyLoop = useCallback(() => {
    if (energyRafRef.current != null) return;
    energyRafRef.current = requestAnimationFrame(stepEnergy);
  }, [stepEnergy]);

  const stop = useCallback(() => {
    requestIdRef.current += 1; // invalidates any in-flight speak() call
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    if (energyRafRef.current != null) {
      cancelAnimationFrame(energyRafRef.current);
      energyRafRef.current = null;
    }
    energyRef.current = 0;
    setIsSpeaking(false);
  }, []);

  const speak = useCallback(
    async (text: string) => {
      const myRequestId = requestIdRef.current + 1;
      stop();
      requestIdRef.current = myRequestId;

      const cleaned = stripMarkdownForSpeech(text);
      if (!cleaned) return;

      let url: string;
      try {
        url = await synthesizeSpeech(cleaned);
      } catch {
        return; // voice unavailable — the chat response itself already succeeded
      }

      if (requestIdRef.current !== myRequestId) {
        URL.revokeObjectURL(url); // superseded while the request was in flight
        return;
      }

      urlRef.current = url;
      if (!audioRef.current) {
        audioRef.current = new Audio();
        audioRef.current.addEventListener("ended", () => setIsSpeaking(false));
      }
      ensureWebAudioGraph(audioRef.current);
      audioCtxRef.current?.resume().catch(() => {});

      audioRef.current.src = url;
      setIsSpeaking(true);
      startEnergyLoop();
      try {
        await audioRef.current.play();
      } catch {
        setIsSpeaking(false);
      }
    },
    [stop, ensureWebAudioGraph, startEnergyLoop]
  );

  useEffect(() => {
    return () => {
      stop();
      audioCtxRef.current?.close().catch(() => {});
    };
  }, [stop]);

  return { speak, stop, isSpeaking, energyRef };
}
