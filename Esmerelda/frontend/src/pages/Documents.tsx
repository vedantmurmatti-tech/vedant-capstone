import { useMemo, useState } from "react";
import { Database } from "lucide-react";
import { useAsync } from "@/lib/useAsync";
import { getCourses, getDocuments } from "@/lib/api";
import { DocumentIndexRow } from "@/components/ui/DocumentIndexRow";
import { SearchBar } from "@/components/ui/SearchBar";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { ListSkeleton } from "@/components/ui/LoadingSkeleton";

export default function Documents() {
  const { data: documents, loading, error, refetch } = useAsync(getDocuments, []);
  const { data: courses } = useAsync(getCourses, []);
  const [query, setQuery] = useState("");
  const [fileType, setFileType] = useState("all");
  const [courseId, setCourseId] = useState("all");

  const fileTypes = useMemo(() => {
    const types = new Set((documents ?? []).map((d) => d.fileType).filter(Boolean) as string[]);
    return Array.from(types).sort();
  }, [documents]);

  const filtered = useMemo(() => {
    let list = documents ?? [];

    const q = query.trim().toLowerCase();
    if (q) list = list.filter((d) => d.name.toLowerCase().includes(q));
    if (fileType !== "all") list = list.filter((d) => d.fileType === fileType);
    if (courseId !== "all") list = list.filter((d) => d.courseId === Number(courseId));

    return list;
  }, [documents, query, fileType, courseId]);

  const hasAnyFilterActive = query || fileType !== "all" || courseId !== "all";

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-display text-xl font-semibold tracking-tight text-warm-50">Knowledge base</h2>
        <p className="mt-1 text-sm text-graphite-400">Every file indexed from Moodle, searchable by Esmerelda.</p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <SearchBar value={query} onChange={setQuery} placeholder="Search documents..." className="sm:w-72" />
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={courseId}
            onChange={(e) => setCourseId(e.target.value)}
            aria-label="Filter by course"
            className="rounded-xl border border-graphite-600/70 bg-graphite-850/70 px-3 py-2 text-sm text-graphite-200 focus:border-cyan-500/60 focus:outline-none"
          >
            <option value="all">All courses</option>
            {(courses ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.shortName ?? c.name}
              </option>
            ))}
          </select>
          <select
            value={fileType}
            onChange={(e) => setFileType(e.target.value)}
            aria-label="Filter by file type"
            className="rounded-xl border border-graphite-600/70 bg-graphite-850/70 px-3 py-2 text-sm text-graphite-200 focus:border-cyan-500/60 focus:outline-none"
          >
            <option value="all">All file types</option>
            {fileTypes.map((type) => (
              <option key={type} value={type}>
                {type.toUpperCase()}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <ListSkeleton count={5} />
      ) : error ? (
        <ErrorState message={error} onRetry={refetch} />
      ) : filtered.length > 0 ? (
        <div className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 px-5">
          {filtered.map((d) => (
            <DocumentIndexRow key={d.id} document={d} />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={Database}
          title={hasAnyFilterActive ? "No documents match" : "No documents yet"}
          description={
            hasAnyFilterActive
              ? "Try a different search term or clear your filters."
              : "Files indexed from Moodle will appear here."
          }
        />
      )}
    </div>
  );
}
