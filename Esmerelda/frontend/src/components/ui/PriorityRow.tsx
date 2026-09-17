import { ExternalLink } from "lucide-react";
import type { Assignment } from "@/types";
import { formatDateTime, getAssignmentUrgency, relativeTimeFromNow, cn } from "@/lib/utils";
import { StatusPill } from "./StatusPill";

const urgencyMeta: Record<
  ReturnType<typeof getAssignmentUrgency>,
  { label: string; tone: "critical" | "warning" | "neutral" | "success"; barColor: string } | null
> = {
  overdue: { label: "Overdue", tone: "critical", barColor: "var(--color-status-critical)" },
  "due-soon": { label: "Due soon", tone: "warning", barColor: "var(--color-status-warning)" },
  upcoming: { label: "Upcoming", tone: "neutral", barColor: "var(--color-graphite-500)" },
  none: null,
};

interface PriorityRowProps {
  assignment: Assignment;
}

export function PriorityRow({ assignment }: PriorityRowProps) {
  const urgency = getAssignmentUrgency(assignment.dueDate);
  const meta = urgencyMeta[urgency];

  return (
    <div className="group relative flex items-start gap-4 border-b border-graphite-700/50 py-4 last:border-b-0">
      <span
        className="mt-1.5 h-full min-h-[2.25rem] w-[2px] shrink-0 self-stretch rounded-full"
        style={{ background: meta?.barColor ?? "var(--color-graphite-600)" }}
        aria-hidden="true"
      />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-xs font-medium uppercase tracking-wide text-cyan-400/90">
            {assignment.courseShortName ?? assignment.courseName}
          </span>
          {meta && (
            <StatusPill tone={meta.tone} dot>
              {meta.label}
            </StatusPill>
          )}
        </div>
        <h3 className="mt-1 truncate font-display text-[15px] font-medium text-warm-50">{assignment.name}</h3>
        {assignment.description && (
          <p className="mt-1 line-clamp-1 text-sm text-graphite-400">{assignment.description}</p>
        )}
        <div className="mt-2 flex items-center gap-2 text-xs text-graphite-400">
          <span>{formatDateTime(assignment.dueDate)}</span>
          <span className="text-graphite-600">·</span>
          <span className={cn(urgency === "overdue" && "text-status-critical")}>
            {relativeTimeFromNow(assignment.dueDate)}
          </span>
        </div>
      </div>

      {assignment.submissionUrl && (
        <a
          href={assignment.submissionUrl}
          aria-label={`Open ${assignment.name}`}
          className="flex shrink-0 items-center gap-1 self-center rounded-lg border border-graphite-600/60 px-2.5 py-1.5 text-xs font-medium text-graphite-300 opacity-0 transition-all hover:border-cyan-500/50 hover:text-cyan-300 group-hover:opacity-100 focus-visible:opacity-100"
        >
          Open <ExternalLink size={12} />
        </a>
      )}
    </div>
  );
}
