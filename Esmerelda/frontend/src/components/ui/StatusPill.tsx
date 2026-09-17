import { cn } from "@/lib/utils";

interface StatusPillProps {
  children: React.ReactNode;
  tone?: "neutral" | "cyan" | "critical" | "warning" | "success";
  dot?: boolean;
  className?: string;
}

const toneStyles: Record<NonNullable<StatusPillProps["tone"]>, string> = {
  neutral: "border-graphite-600/70 bg-graphite-800/60 text-graphite-300",
  cyan: "border-cyan-500/30 bg-cyan-500/10 text-cyan-300",
  critical: "border-status-critical/30 bg-status-critical/10 text-status-critical",
  warning: "border-status-warning/30 bg-status-warning/10 text-status-warning",
  success: "border-status-success/30 bg-status-success/10 text-status-success",
};

const dotStyles: Record<NonNullable<StatusPillProps["tone"]>, string> = {
  neutral: "bg-graphite-400",
  cyan: "bg-cyan-400",
  critical: "bg-status-critical",
  warning: "bg-status-warning",
  success: "bg-status-success",
};

export function StatusPill({ children, tone = "neutral", dot = false, className }: StatusPillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wide",
        toneStyles[tone],
        className
      )}
    >
      {dot && <span className={cn("size-1.5 rounded-full", dotStyles[tone])} />}
      {children}
    </span>
  );
}
