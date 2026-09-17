import type { LucideIcon } from "lucide-react";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="animate-[var(--animate-fade-in)] flex flex-col items-center justify-center rounded-2xl border border-graphite-700/60 bg-graphite-850/40 px-6 py-16 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-full border border-graphite-600/60 bg-graphite-800 text-graphite-400">
        <Icon size={20} strokeWidth={1.75} />
      </div>
      <h3 className="font-display text-base font-medium text-warm-50">{title}</h3>
      {description && <p className="mt-1.5 max-w-sm text-sm text-graphite-400">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
