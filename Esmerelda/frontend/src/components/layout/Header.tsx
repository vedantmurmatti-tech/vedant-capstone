import { useEffect, useState } from "react";
import { Menu, RadioTower, RefreshCw, AlertTriangle, Loader2 } from "lucide-react";
import { useAsync } from "@/lib/useAsync";
import { getMoodleSyncStatus } from "@/lib/api";
import { relativeTimeFromNow, cn } from "@/lib/utils";
import type { MoodleSyncState } from "@/types";

const syncMeta: Record<MoodleSyncState, { label: string; icon: typeof RadioTower; className: string }> = {
  synced: { label: "System synced", icon: RadioTower, className: "text-status-success" },
  syncing: { label: "Syncing", icon: Loader2, className: "text-cyan-400 animate-spin" },
  stale: { label: "Needs sync", icon: RefreshCw, className: "text-status-warning" },
  error: { label: "Sync error", icon: AlertTriangle, className: "text-status-critical" },
};

interface HeaderProps {
  title: string;
  onMenuClick: () => void;
}

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000 * 30);
    return () => clearInterval(id);
  }, []);
  return now;
}

export function Header({ title, onMenuClick }: HeaderProps) {
  const { data: status, error: statusError } = useAsync(getMoodleSyncStatus, []);
  const meta = statusError
    ? { label: "Backend offline", icon: AlertTriangle, className: "text-status-critical" }
    : status
      ? syncMeta[status.state]
      : null;
  const SyncIcon = meta?.icon;
  const now = useClock();

  return (
    <header className="sticky top-0 z-20 flex items-center justify-between border-b border-graphite-700/60 bg-graphite-950/80 px-4 py-3.5 backdrop-blur-md sm:px-6">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onMenuClick}
          aria-label="Open sidebar"
          className="rounded-lg p-1.5 text-graphite-300 hover:bg-graphite-700/60 hover:text-warm-50 lg:hidden"
        >
          <Menu size={20} />
        </button>
        <h1 className="font-display text-[15px] font-medium tracking-wide text-graphite-200 sm:text-base">
          {title}
        </h1>
      </div>

      <div className="flex items-center gap-3 sm:gap-4">
        <span className="hidden font-mono text-xs tabular-nums text-graphite-400 sm:inline">
          {now.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
        </span>
        {meta && SyncIcon && (
          <div
            className={cn(
              "flex items-center gap-2 rounded-full border border-graphite-700/70 bg-graphite-850/70 px-3 py-1.5 text-xs font-medium",
              meta.className
            )}
            title={status?.lastSyncedAt ? `Last synced ${relativeTimeFromNow(status.lastSyncedAt)}` : undefined}
          >
            <SyncIcon size={13} strokeWidth={2} />
            <span className="hidden sm:inline">{meta.label}</span>
          </div>
        )}
      </div>
    </header>
  );
}
