import { cn } from "@/lib/utils";

export interface FilterOption {
  label: string;
  value: string;
}

interface FilterControlsProps {
  options: FilterOption[];
  active: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
}

export function FilterControls({ options, active, onChange, ariaLabel }: FilterControlsProps) {
  return (
    <div
      role="group"
      aria-label={ariaLabel ?? "Filters"}
      className="flex flex-wrap items-center gap-1 rounded-xl border border-graphite-700/60 bg-graphite-850/60 p-1"
    >
      {options.map((option) => {
        const isActive = option.value === active;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={isActive}
            onClick={() => onChange(option.value)}
            className={cn(
              "rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
              isActive
                ? "bg-cyan-500/15 text-cyan-300"
                : "text-graphite-400 hover:bg-graphite-700/50 hover:text-warm-50"
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
