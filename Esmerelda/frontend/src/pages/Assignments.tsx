import { useMemo, useState } from "react";
import { ClipboardList } from "lucide-react";
import { useAsync } from "@/lib/useAsync";
import { getAssignments } from "@/lib/api";
import { PriorityRow } from "@/components/ui/PriorityRow";
import { SearchBar } from "@/components/ui/SearchBar";
import { FilterControls } from "@/components/ui/FilterControls";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { ListSkeleton } from "@/components/ui/LoadingSkeleton";
import { getAssignmentUrgency } from "@/lib/utils";

type FilterValue = "all" | "overdue" | "due-soon" | "upcoming";
type SortValue = "due-asc" | "due-desc";

const filterOptions: { label: string; value: FilterValue }[] = [
  { label: "All", value: "all" },
  { label: "Overdue", value: "overdue" },
  { label: "Due soon", value: "due-soon" },
  { label: "Upcoming", value: "upcoming" },
];

const sortOptions: { label: string; value: SortValue }[] = [
  { label: "Soonest first", value: "due-asc" },
  { label: "Latest first", value: "due-desc" },
];

export default function Assignments() {
  const { data: assignments, loading, error, refetch } = useAsync(getAssignments, []);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterValue>("all");
  const [sort, setSort] = useState<SortValue>("due-asc");

  const filtered = useMemo(() => {
    let list = assignments ?? [];

    const q = query.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (a) => a.name.toLowerCase().includes(q) || a.courseName.toLowerCase().includes(q)
      );
    }

    if (filter !== "all") {
      list = list.filter((a) => getAssignmentUrgency(a.dueDate) === filter);
    }

    list = [...list].sort((a, b) => {
      const aTime = new Date(a.dueDate ?? 0).getTime();
      const bTime = new Date(b.dueDate ?? 0).getTime();
      return sort === "due-asc" ? aTime - bTime : bTime - aTime;
    });

    return list;
  }, [assignments, query, filter, sort]);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-display text-xl font-semibold tracking-tight text-warm-50">Priority feed</h2>
        <p className="mt-1 text-sm text-graphite-400">Everything due, ranked by urgency.</p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <SearchBar value={query} onChange={setQuery} placeholder="Search assignments..." className="sm:w-72" />
        <div className="flex flex-wrap items-center gap-2">
          <FilterControls options={filterOptions} active={filter} onChange={(v) => setFilter(v as FilterValue)} ariaLabel="Filter by status" />
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortValue)}
            aria-label="Sort assignments"
            className="rounded-xl border border-graphite-600/70 bg-graphite-850/70 px-3 py-2 text-sm text-graphite-200 focus:border-cyan-500/60 focus:outline-none"
          >
            {sortOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <ListSkeleton count={6} />
      ) : error ? (
        <ErrorState message={error} onRetry={refetch} />
      ) : filtered.length > 0 ? (
        <div className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 px-5">
          {filtered.map((a) => (
            <PriorityRow key={a.id} assignment={a} />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={ClipboardList}
          title="No assignments found"
          description={query || filter !== "all" ? "Try adjusting your search or filters." : "Nothing tracked yet."}
        />
      )}
    </div>
  );
}
