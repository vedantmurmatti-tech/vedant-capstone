import { useRef, useState } from "react";
import { Construction, Mail, User, Volume2 } from "lucide-react";
import { useAsync } from "@/lib/useAsync";
import { getStudentName, synthesizeSpeech } from "@/lib/api";

const TEST_SENTENCE = "Good evening, Vedant. How may I assist you?";

export default function Settings() {
  const { data: name } = useAsync(getStudentName, []);
  const [voiceState, setVoiceState] = useState<"idle" | "loading" | "error">("idle");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);

  async function testVoice() {
    setVoiceState("loading");
    setVoiceError(null);
    try {
      const url = await synthesizeSpeech(TEST_SENTENCE);
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = url;
      if (!audioRef.current) audioRef.current = new Audio();
      audioRef.current.src = url;
      await audioRef.current.play();
      setVoiceState("idle");
    } catch (err) {
      setVoiceState("error");
      setVoiceError(err instanceof Error ? err.message : "Couldn't play Esmerelda's voice.");
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h2 className="font-display text-xl font-semibold tracking-tight text-warm-50">Settings</h2>
        <p className="mt-1 text-sm text-graphite-400">Your Esmerelda profile and preferences.</p>
      </div>

      <section className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 p-5">
        <h3 className="mb-4 text-xs font-medium uppercase tracking-wide text-graphite-500">Profile</h3>
        <div className="space-y-3">
          <div className="flex items-center gap-3 border-b border-graphite-700/50 pb-3">
            <User size={15} className="text-graphite-500" />
            <span className="text-sm text-graphite-300">Name</span>
            <span className="ml-auto text-sm text-warm-50">{name ?? "—"}</span>
          </div>
          <div className="flex items-center gap-3">
            <Mail size={15} className="text-graphite-500" />
            <span className="text-sm text-graphite-300">Email</span>
            <span className="ml-auto truncate text-sm text-warm-50">student.design1@flame.edu.in</span>
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 p-5">
        <h3 className="mb-4 text-xs font-medium uppercase tracking-wide text-graphite-500">Voice (test)</h3>
        <p className="mb-3 text-sm text-graphite-400">
          Plays a test sentence through Esmerelda's voice. Responses aren't spoken automatically yet.
        </p>
        <button
          type="button"
          onClick={testVoice}
          disabled={voiceState === "loading"}
          className="flex items-center gap-2 rounded-xl border border-graphite-600/70 bg-graphite-850/70 px-3 py-2 text-sm font-medium text-graphite-200 transition-colors hover:border-graphite-500 hover:bg-graphite-800 disabled:opacity-60"
        >
          <Volume2 size={15} />
          {voiceState === "loading" ? "Generating…" : "Test Esmerelda Voice"}
        </button>
        {voiceState === "error" && voiceError && (
          <p className="mt-2 text-xs text-status-critical">{voiceError}</p>
        )}
      </section>

      <section className="flex items-start gap-3 rounded-2xl border border-graphite-700/60 bg-graphite-850/40 p-5">
        <Construction size={18} className="mt-0.5 shrink-0 text-graphite-500" />
        <div>
          <h3 className="text-sm font-medium text-warm-50">More preferences are on the way</h3>
          <p className="mt-1 text-sm text-graphite-400">
            Notification controls, sync frequency, and voice-input configuration will land here once the backend
            supports them.
          </p>
        </div>
      </section>
    </div>
  );
}
