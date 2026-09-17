import { AlertTriangle, RotateCcw } from "lucide-react";

interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ title = "Couldn't load this", message, onRetry }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-status-critical/30 bg-status-critical/5 px-6 py-14 text-center">
      <div className="flex size-12 items-center justify-center rounded-full border border-status-critical/30 bg-status-critical/10 text-status-critical">
        <AlertTriangle size={20} strokeWidth={1.75} />
      </div>
      <div>
        <h3 className="font-display text-base font-medium text-warm-50">{title}</h3>
        <p className="mt-1.5 max-w-sm text-sm text-graphite-400">{message}</p>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 flex items-center gap-1.5 rounded-lg border border-graphite-600/70 px-3 py-1.5 text-xs font-medium text-graphite-200 transition-colors hover:border-status-critical/40 hover:text-warm-50"
        >
          <RotateCcw size={12} /> Retry
        </button>
      )}
    </div>
  );
}
