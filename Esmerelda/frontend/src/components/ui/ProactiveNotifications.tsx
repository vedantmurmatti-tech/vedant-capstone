import { Link } from "react-router-dom";
import { AlertTriangle, Clock, Sparkles } from "lucide-react";
import type { ProactiveNotification } from "@/types";
import { cn } from "@/lib/utils";

/**
 * The one reusable presentation for backend/api/notifications.py's
 * `ProactiveNotificationOut` list — kept separate from Dashboard.tsx so
 * academic notification text/styling lives in exactly one place, not
 * hardcoded inline (Batch 2's own instruction). Every message here was
 * already fully composed server-side from real stored data; this
 * component only chooses an icon/color per `kind` and renders it.
 */
export function ProactiveNotifications({ notifications }: { notifications: ProactiveNotification[] }) {
  if (notifications.length === 0) return null;

  return (
    <div className="flex flex-wrap justify-center gap-2">
      {notifications.map((n) => {
        const Icon = n.kind === "sync_error" ? AlertTriangle : n.kind === "due_soon" ? Clock : Sparkles;
        const tone =
          n.kind === "sync_error"
            ? "border-status-critical/30 bg-status-critical/5 text-status-critical"
            : n.kind === "due_soon"
              ? "border-status-warning/30 bg-status-warning/5 text-status-warning"
              : "border-cyan-500/30 bg-cyan-500/5 text-cyan-300";

        const content = (
          <span className={cn("flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium", tone)}>
            <Icon size={12} strokeWidth={2} />
            {n.message}
          </span>
        );

        return n.courseId ? (
          <Link key={n.id} to={`/courses/${n.courseId}`} className="transition-opacity hover:opacity-80">
            {content}
          </Link>
        ) : (
          <span key={n.id}>{content}</span>
        );
      })}
    </div>
  );
}
