import { useCallback, useEffect, useRef, useState } from "react";
import { synthesizeSpeech } from "@/lib/api";
import { simplifyMoodleCourseIdentifiersForSpeech, stripMarkdownForSpeech } from "@/lib/utils";

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
  const debugFrameRef = useRef(0);

  // Creates the one shared <audio> element `speak()` reuses for every
  // response, if it doesn't already exist. Split out of `speak()` so the
  // gesture-priming effect below can call it too, without ever creating a
  // second element (createMediaElementSource below is a one-per-element,
  // one-time-ever operation).
  const ensureAudioElement = useCallback(() => {
    if (!audioRef.current) {
      audioRef.current = new Audio();
      audioRef.current.addEventListener("ended", () => setIsSpeaking(false));
    }
    return audioRef.current;
  }, []);

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
        if (import.meta.env.DEV) {
          console.debug("[EsmereldaSpeech] Web Audio graph ready", {
            contextState: ctx.state,
            fftSize: analyser.fftSize,
          });
        }
      } catch (err) {
        // The element's output was already captured by the graph above —
        // reconnect straight to the speakers so audio isn't silently
        // lost, just without any energy analysis.
        source.connect(ctx.destination);
        if (import.meta.env.DEV) console.warn("[EsmereldaSpeech] AnalyserNode wiring failed; audio still plays.", err);
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

    // Dev-only, throttled to ~2/sec — never logs audio content, just the
    // handful of booleans/numbers needed to see exactly where a real
    // browser's pipeline stops producing data. Stripped from production
    // builds entirely (import.meta.env.DEV is statically false there).
    if (import.meta.env.DEV) {
      debugFrameRef.current += 1;
      if (debugFrameRef.current % 30 === 0) {
        console.debug("[EsmereldaSpeech] tick", {
          isSpeaking: stillPlaying,
          audioElementExists: !!audio,
          audioPaused: audio?.paused,
          audioContextState: audioCtxRef.current?.state ?? "none",
          hasAnalyser: !!analyser,
          energy: Number(energyRef.current.toFixed(3)),
        });
      }
    }

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

      const cleaned = simplifyMoodleCourseIdentifiersForSpeech(stripMarkdownForSpeech(text));
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
      const audioEl = ensureAudioElement();
      ensureWebAudioGraph(audioEl);
      // Browsers that start a new AudioContext "suspended" until a user
      // gesture only actually resume it from within/soon-after a real
      // gesture — by the time this line runs, `speak()` has already
      // awaited a network round trip, so the gesture-priming effect below
      // (bound directly to the click/keypress that sent this message) is
      // what actually gets the context running in practice. This call is
      // just a harmless, defensive second attempt.
      audioCtxRef.current?.resume().catch(() => {});

      audioEl.src = url;
      setIsSpeaking(true);
      startEnergyLoop();
      try {
        await audioEl.play();
      } catch {
        setIsSpeaking(false);
      }
    },
    [stop, ensureAudioElement, ensureWebAudioGraph, startEnergyLoop]
  );

  // Ties AudioContext creation/resume directly to the first real user
  // gesture on the page (a click or keypress — e.g. the very "Enter" that
  // sends the first chat message), rather than only ever attempting it
  // deep inside speak()'s async continuation after a network round trip.
  // This is the standard fix for a real browser leaving a freshly-created
  // AudioContext in "suspended" state (silently producing no analyser
  // data, even though audio.play() itself can still succeed) when it's
  // only ever created/resumed outside a gesture's call stack.
  useEffect(() => {
    function primeOnFirstGesture() {
      const audioEl = ensureAudioElement();
      ensureWebAudioGraph(audioEl);
      audioCtxRef.current?.resume().catch(() => {});
    }
    window.addEventListener("pointerdown", primeOnFirstGesture, { once: true });
    window.addEventListener("keydown", primeOnFirstGesture, { once: true });
    return () => {
      window.removeEventListener("pointerdown", primeOnFirstGesture);
      window.removeEventListener("keydown", primeOnFirstGesture);
    };
  }, [ensureAudioElement, ensureWebAudioGraph]);

  useEffect(() => {
    return () => {
      stop();
      audioCtxRef.current?.close().catch(() => {});
    };
  }, [stop]);

  return { speak, stop, isSpeaking, energyRef };
}
