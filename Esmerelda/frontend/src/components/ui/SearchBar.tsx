import { Search, X } from "lucide-react";

interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

export function SearchBar({ value, onChange, placeholder = "Search...", className }: SearchBarProps) {
  return (
    <div className={`group relative flex items-center ${className ?? ""}`}>
      <Search
        size={15}
        className="pointer-events-none absolute left-3.5 text-graphite-500 group-focus-within:text-cyan-400"
      />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="w-full rounded-xl border border-graphite-600/70 bg-graphite-850/70 py-2.5 pl-10 pr-9 text-sm text-warm-50 placeholder:text-graphite-500 transition-colors focus:border-cyan-500/60 focus:outline-none"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute right-3 text-graphite-500 transition-colors hover:text-warm-50"
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}
