import { useCallback, useEffect, useRef, useState } from "react";

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

interface UseVoiceInputResult {
  state: VoiceInputState;
  /** The most recently completed recording, or null before any recording has finished. */
  audioBlob: Blob | null;
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

  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mountedRef = useRef(true);

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop(); // onstop (below) finishes the state transition and releases the mic
    }
  }, []);

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
    const mimeType = window.MediaRecorder.isTypeSupported?.("audio/webm") ? "audio/webm" : undefined;
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
      releaseStream();
      if (mountedRef.current) {
        setAudioBlob(blob);
        setState("stopped");
      }
    };

    recorderRef.current = recorder;
    recorder.start();
    setState("listening");
  }, [stop, releaseStream]);

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

  return { state, audioBlob, start, stop, reset };
}
