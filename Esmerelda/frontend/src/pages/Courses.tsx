import { useMemo, useState } from "react";
import { BookOpen } from "lucide-react";
import { useAsync } from "@/lib/useAsync";
import { getAssignments, getCourses } from "@/lib/api";
import { CourseWorkspaceCard } from "@/components/ui/CourseWorkspaceCard";
import { SearchBar } from "@/components/ui/SearchBar";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { CardSkeletonGrid } from "@/components/ui/LoadingSkeleton";

export default function Courses() {
  const [query, setQuery] = useState("");
  const {
    data: courses,
    loading: coursesLoading,
    error: coursesError,
    refetch: refetchCourses,
  } = useAsync(getCourses, []);
  const { data: assignments } = useAsync(getAssignments, []);

  const assignmentCountByCourse = useMemo(() => {
    const map = new Map<number, number>();
    for (const a of assignments ?? []) {
      map.set(a.courseId, (map.get(a.courseId) ?? 0) + 1);
    }
    return map;
  }, [assignments]);

  const filtered = (courses ?? []).filter((c) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return c.name.toLowerCase().includes(q) || (c.shortName ?? "").toLowerCase().includes(q);
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-display text-xl font-semibold tracking-tight text-warm-50">Active workspaces</h2>
          <p className="mt-1 text-sm text-graphite-400">Every course tracked this term.</p>
        </div>
        <SearchBar value={query} onChange={setQuery} placeholder="Search courses..." className="sm:w-72" />
      </div>

      {coursesLoading ? (
        <CardSkeletonGrid count={4} />
      ) : coursesError ? (
        <ErrorState message={coursesError} onRetry={refetchCourses} />
      ) : filtered.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((course) => (
            <CourseWorkspaceCard
              key={course.id}
              course={course}
              assignmentCount={assignmentCountByCourse.get(course.id) ?? 0}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={BookOpen}
          title={query ? "No courses match your search" : "No courses yet"}
          description={query ? "Try a different search term." : "Courses will appear here once Moodle sync runs."}
        />
      )}
    </div>
  );
}
