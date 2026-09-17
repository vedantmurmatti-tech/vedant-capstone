import { useRef } from "react";
import { ArrowUp, Mic, CornerDownLeft } from "lucide-react";
import { cn } from "@/lib/utils";

interface CommandInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  loading?: boolean;
  variant?: "console" | "docked";
  autoFocus?: boolean;
}

export function CommandInput({
  value,
  onChange,
  onSubmit,
  placeholder = "How can I help you today?",
  loading = false,
  variant = "console",
  autoFocus = false,
}: CommandInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isConsole = variant === "console";

  function handleSubmit() {
    if (!value.trim() || loading) return;
    onSubmit();
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        handleSubmit();
      }}
      className={cn(
        "group relative flex items-end gap-2 rounded-2xl border border-graphite-600/70 bg-graphite-850/70 backdrop-blur-md transition-all duration-300",
        "focus-within:border-cyan-500/60",
        isConsole ? "px-5 py-4" : "px-4 py-3"
      )}
      style={{
        boxShadow: "0 0 0 0 transparent",
      }}
    >
      <div
        className="pointer-events-none absolute inset-0 rounded-2xl opacity-0 transition-opacity duration-300 group-focus-within:opacity-100"
        style={{
          boxShadow: "0 0 0 1px color-mix(in oklab, var(--color-cyan-500) 45%, transparent), 0 0 28px color-mix(in oklab, var(--color-cyan-500) 22%, transparent)",
        }}
        aria-hidden="true"
      />

      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
          }
        }}
        placeholder={placeholder}
        aria-label={placeholder}
        autoFocus={autoFocus}
        rows={1}
        className={cn(
          "relative z-10 flex-1 resize-none bg-transparent text-warm-50 placeholder:text-graphite-400 focus:outline-none",
          isConsole ? "max-h-32 py-1 text-[15px]" : "max-h-40 py-1.5 text-sm"
        )}
      />

      <div className="relative z-10 flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          disabled
          aria-label="Voice input (not yet implemented)"
          title="Voice input — coming soon"
          className="flex size-9 items-center justify-center rounded-xl text-graphite-500 transition-colors hover:bg-graphite-700/50 disabled:cursor-not-allowed"
        >
          <Mic size={16} />
        </button>
        <button
          type="submit"
          disabled={!value.trim() || loading}
          aria-label="Send command"
          className={cn(
            "flex size-9 items-center justify-center rounded-xl transition-all",
            value.trim() && !loading
              ? "bg-cyan-500 text-graphite-950 hover:bg-cyan-400"
              : "bg-graphite-700/60 text-graphite-500"
          )}
        >
          <ArrowUp size={16} strokeWidth={2.5} />
        </button>
      </div>

      {isConsole && (
        <div className="pointer-events-none absolute -bottom-6 right-1 hidden items-center gap-1 text-[11px] text-graphite-500 sm:flex">
          <CornerDownLeft size={11} /> to send · Shift + Enter for a new line
        </div>
      )}
    </form>
  );
}
