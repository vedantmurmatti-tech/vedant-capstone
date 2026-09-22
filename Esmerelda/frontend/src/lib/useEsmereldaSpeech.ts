import { useCallback, useEffect, useRef, useState } from "react";
import { synthesizeSpeech } from "@/lib/api";
import { stripMarkdownForSpeech } from "@/lib/utils";

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
 */
export function useEsmereldaSpeech() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const requestIdRef = useRef(0);
  const [isSpeaking, setIsSpeaking] = useState(false);

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
      audioRef.current.src = url;
      setIsSpeaking(true);
      try {
        await audioRef.current.play();
      } catch {
        setIsSpeaking(false);
      }
    },
    [stop]
  );

  useEffect(() => stop, [stop]);

  return { speak, stop, isSpeaking };
}
