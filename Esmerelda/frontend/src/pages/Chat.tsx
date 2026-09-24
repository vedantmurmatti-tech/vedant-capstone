import { useEffect, useRef, useState, type RefObject } from "react";
import { Link, useLocation } from "react-router-dom";
import { Plus, WifiOff, FileText, ArrowUpRight, Download, ClipboardList, BookOpen } from "lucide-react";
import { getDocumentDownloadUrl, sendChatMessage } from "@/lib/api";
import { suggestedPrompts } from "@/lib/mockData";
import type { Assignment, ChatMessage, ChatSource, Course, DocumentFile } from "@/types";
import { AiCore } from "@/components/core/AiCore";
import { CommandInput } from "@/components/ui/CommandInput";
import { ChatMarkdown } from "@/components/ui/ChatMarkdown";
import { formatDateTime, getAssignmentUrgency, cn } from "@/lib/utils";
import { useEsmereldaSpeech } from "@/lib/useEsmereldaSpeech";
import { appendVoiceTurn, getVoiceConversation, resetVoiceConversation } from "@/lib/voiceConversation";

// Same cap Dashboard.tsx's voice loop uses — kept in sync manually since
// each page trims client-side before sending (the backend independently
// caps again regardless of what either client sends).
const HISTORY_MAX_MESSAGES = 8;

// What Dashboard.tsx hands off via `navigate("/chat", { state: { voiceHandoff } })`
// when the reasoning layer decides a voice exchange needs a visual surface
// (see BUILD_LOG.md's "Batch 1" entry) — an already-computed exchange, never
// re-sent to the backend here, so arriving via voice never duplicates the
// Groq/Gemini call or risks a different answer than what was already spoken/decided.
interface VoiceHandoff {
  userMessage: string;
  reply: string;
  assignments: Assignment[];
  courses: Course[];
  documents: DocumentFile[];
  sources: ChatSource[];
  followUps: string[];
}

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export default function Chat() {
  const location = useLocation();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [reachable, setReachable] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const consumedInitial = useRef(false);
  const consumedVoiceHandoff = useRef(false);
  // True only once this Conversations session has actually received a
  // voice handoff (or the user continues typing after one) — until then,
  // submitMessage() sends exactly the same request it always has (no
  // conversationId/history), so a typed-only session is byte-for-byte
  // unaffected by any of this. See lib/voiceConversation.ts.
  const hasVoiceContextRef = useRef(false);
  const { speak, stop: stopSpeaking, isSpeaking, energyRef } = useEsmereldaSpeech();

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, pending]);

  useEffect(() => {
    const state = location.state as { initialMessage?: string; voiceHandoff?: VoiceHandoff } | null;

    if (state?.voiceHandoff && !consumedVoiceHandoff.current) {
      consumedVoiceHandoff.current = true;
      hasVoiceContextRef.current = true;
      const { userMessage, reply, assignments, courses, documents, sources, followUps } = state.voiceHandoff;
      const now = new Date().toISOString();
      // Rendered directly — never re-sent to sendChatMessage(). This is
      // the exact answer Esmerelda already decided on (and the user
      // already asked, by voice) a moment ago; re-asking would risk a
      // different answer and waste a real reasoning-layer call.
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "user", content: userMessage, createdAt: now },
        {
          id: newId(),
          role: "assistant",
          content: reply,
          createdAt: now,
          assignments,
          courses,
          documents,
          sources,
          followUps,
        },
      ]);
      return;
    }

    const initial = state?.initialMessage;
    if (initial && !consumedInitial.current) {
      consumedInitial.current = true;
      submitMessage(initial);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

  async function submitMessage(content: string) {
    const trimmed = content.trim();
    if (!trimmed || pending) return;

    setInput("");
    const userMessage: ChatMessage = {
      id: newId(),
      role: "user",
      content: trimmed,
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setPending(true);

    try {
      // Only ever populated once this session has actually involved voice
      // (a handoff arrived, or the user kept typing after one) — a purely
      // typed session sends exactly what it always has.
      const res = hasVoiceContextRef.current
        ? await sendChatMessage(trimmed, getVoiceConversation())
        : await sendChatMessage(trimmed);
      setReachable(true);
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: res.reply,
          createdAt: new Date().toISOString(),
          sources: res.sources,
          followUps: res.followUps,
          assignments: res.assignments,
          courses: res.courses,
          documents: res.documents,
        },
      ]);
      if (hasVoiceContextRef.current) {
        appendVoiceTurn(trimmed, res.reply, HISTORY_MAX_MESSAGES);
      }
      // Speaks only the final reply text — never the user's message, the
      // "Thinking…" state above, or the error branch below. speak() stops
      // any still-playing previous response first and never throws, so a
      // voice failure here can't affect the chat response already shown.
      void speak(res.reply);
    } catch (err) {
      setReachable(false);
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: err instanceof Error ? err.message : "Esmerelda's AI backend isn't reachable right now.",
          createdAt: new Date().toISOString(),
          unavailable: true,
        },
      ]);
    } finally {
      setPending(false);
    }
  }

  function handleNewConversation() {
    stopSpeaking();
    setMessages([]);
    setInput("");
    // A genuinely new conversation also severs any inherited voice
    // context — otherwise "New conversation" here would still quietly
    // carry the old Orb exchange into whatever's typed next.
    hasVoiceContextRef.current = false;
    resetVoiceConversation();
  }

  return (
    <div className="flex h-[calc(100vh-8.5rem)] flex-col">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <AiCore
            size="sm"
            state={pending ? "processing" : isSpeaking ? "active" : "idle"}
            energyRef={energyRef}
          />
          <div>
            <h2 className="font-display text-lg font-semibold tracking-tight text-warm-50">Esmerelda</h2>
            <p className="flex items-center gap-1.5 text-xs text-graphite-500">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  pending || isSpeaking ? "animate-pulse bg-cyan-400" : reachable ? "bg-status-success" : "bg-status-critical"
                )}
              />
              {pending ? "Thinking…" : isSpeaking ? "Speaking…" : reachable ? "Online" : "Unreachable"} · Gemini,
              grounded in your real Moodle data
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={handleNewConversation}
          className="flex items-center gap-1.5 rounded-xl border border-graphite-600/70 bg-graphite-850/70 px-3 py-2 text-sm font-medium text-graphite-200 transition-colors hover:border-graphite-500 hover:bg-graphite-800"
        >
          <Plus size={15} /> New conversation
        </button>
      </div>

      <div
        ref={scrollRef}
        className="flex-1 space-y-5 overflow-y-auto rounded-2xl border border-graphite-700/60 bg-graphite-850/30 p-4 sm:p-6"
      >
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <AiCore size="md" state="idle" />
            <h3 className="mt-4 font-display text-base font-medium text-warm-50">What do you want to know?</h3>
            <p className="mt-1 max-w-sm text-sm text-graphite-400">
              Ask about deadlines, a course by name, or indexed documents — answered from your real synced data.
            </p>
            <div className="mt-5 flex max-w-lg flex-wrap justify-center gap-2">
              {suggestedPrompts.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => submitMessage(prompt)}
                  className="rounded-full border border-graphite-600/60 bg-graphite-850/70 px-3.5 py-2 text-sm text-graphite-300 transition-all hover:border-cyan-500/50 hover:text-cyan-200"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((message, idx) => (
              <MessageBubble
                key={message.id}
                message={message}
                onFollowUp={submitMessage}
                speaking={isSpeaking && idx === messages.length - 1}
                energyRef={energyRef}
              />
            ))}
            {pending && (
              <div className="flex items-start gap-3">
                <AiCore size="sm" state="processing" />
                <div className="flex items-center gap-1 rounded-2xl rounded-tl-sm border border-graphite-700/70 bg-graphite-850 px-4 py-3">
                  <span className="size-1.5 animate-bounce rounded-full bg-cyan-400/80 [animation-delay:-0.3s]" />
                  <span className="size-1.5 animate-bounce rounded-full bg-cyan-400/80 [animation-delay:-0.15s]" />
                  <span className="size-1.5 animate-bounce rounded-full bg-cyan-400/80" />
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <div className="mt-4">
        <CommandInput
          value={input}
          onChange={setInput}
          onSubmit={() => submitMessage(input)}
          placeholder="Message Esmerelda..."
          loading={pending}
          variant="docked"
        />
        <p className="mt-1.5 px-1 text-[11px] text-graphite-500">
          <kbd className="rounded border border-graphite-700 px-1 py-0.5 font-mono">Enter</kbd> to send ·{" "}
          <kbd className="rounded border border-graphite-700 px-1 py-0.5 font-mono">Shift + Enter</kbd> for a new line
        </p>
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  onFollowUp,
  speaking,
  energyRef,
}: {
  message: ChatMessage;
  onFollowUp: (text: string) => void;
  /** True only for the most recent assistant message while its reply is actually being spoken. */
  speaking: boolean;
  energyRef: RefObject<number>;
}) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex items-start justify-end gap-2.5">
        <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-cyan-500/15 px-4 py-3 text-[14px] leading-relaxed text-warm-50">
          {message.content}
        </div>
      </div>
    );
  }

  if (message.unavailable) {
    return (
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-full border border-status-warning/30 bg-status-warning/10 text-status-warning">
          <WifiOff size={15} />
        </div>
        <div className="max-w-[78%] rounded-2xl rounded-tl-sm border border-dashed border-status-warning/30 bg-status-warning/5 px-4 py-3.5 text-[14px] leading-relaxed text-graphite-300">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3">
      <AiCore size="sm" state={speaking ? "active" : "idle"} energyRef={speaking ? energyRef : undefined} />
      <div className="max-w-[78%] space-y-3">
        <div className="rounded-2xl rounded-tl-sm border border-graphite-700/70 bg-graphite-850 px-4 py-3.5">
          <ChatMarkdown content={message.content} />
        </div>

        {message.assignments && message.assignments.length > 0 && (
          <div className="space-y-1.5">
            {message.assignments.map((a) => {
              const urgency = getAssignmentUrgency(a.dueDate);
              return (
                <Link
                  key={a.id}
                  to={`/courses/${a.courseId}`}
                  className="flex items-center gap-2.5 rounded-lg border border-graphite-700/60 bg-graphite-850/70 px-3 py-2 text-xs transition-colors hover:border-cyan-500/40"
                >
                  <ClipboardList size={12} className="shrink-0 text-graphite-500" />
                  <span
                    className={cn(
                      "size-1.5 shrink-0 rounded-full",
                      urgency === "overdue"
                        ? "bg-status-critical"
                        : urgency === "due-soon"
                          ? "bg-status-warning"
                          : "bg-graphite-500"
                    )}
                  />
                  <span className="min-w-0 flex-1 truncate text-graphite-200">{a.name}</span>
                  <span className="shrink-0 text-graphite-500">{formatDateTime(a.dueDate)}</span>
                </Link>
              );
            })}
          </div>
        )}

        {message.courses && message.courses.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.courses.map((c) => (
              <Link
                key={c.id}
                to={`/courses/${c.id}`}
                className="inline-flex items-center gap-1.5 rounded-lg border border-graphite-700/60 bg-graphite-850/70 px-2.5 py-1.5 text-xs text-graphite-300 transition-colors hover:border-cyan-500/40 hover:text-cyan-200"
              >
                <BookOpen size={11} /> {c.shortName ?? c.name}
              </Link>
            ))}
          </div>
        )}

        {message.documents && message.documents.length > 0 && (
          <div className="space-y-1.5">
            {message.documents.map((d) => (
              <a
                key={d.id}
                href={getDocumentDownloadUrl(d)}
                className="flex items-center gap-2.5 rounded-lg border border-graphite-700/60 bg-graphite-850/70 px-3 py-2 text-xs transition-colors hover:border-cyan-500/40"
              >
                <FileText size={12} className="shrink-0 text-graphite-500" />
                <span className="min-w-0 flex-1 truncate text-graphite-200">{d.name}</span>
                <Download size={11} className="shrink-0 text-graphite-500" />
              </a>
            ))}
          </div>
        )}

        {message.sources && message.sources.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.sources.map((s, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1.5 rounded-lg border border-graphite-700/70 bg-graphite-850/70 px-2.5 py-1 text-[11px] text-graphite-400"
              >
                <FileText size={11} /> {s.label}
              </span>
            ))}
          </div>
        )}

        {message.followUps && message.followUps.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.followUps.map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => onFollowUp(f)}
                className="inline-flex items-center gap-1 rounded-lg border border-graphite-700/60 px-2.5 py-1.5 text-xs text-graphite-300 transition-colors hover:border-cyan-500/50 hover:text-cyan-200"
              >
                {f} <ArrowUpRight size={11} />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
