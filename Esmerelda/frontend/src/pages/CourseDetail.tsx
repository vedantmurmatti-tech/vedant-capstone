import { useParams, Link } from "react-router-dom";
import { ArrowLeft, ClipboardList, FileText } from "lucide-react";
import { useAsync } from "@/lib/useAsync";
import { getAssignmentsByCourse, getCourseById, getResourcesByCourse } from "@/lib/api";
import { PriorityRow } from "@/components/ui/PriorityRow";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { ListSkeleton, Skeleton } from "@/components/ui/LoadingSkeleton";
import { initialsFromName } from "@/lib/utils";

const resourceLabel: Record<string, string> = {
  activity: "Activity",
  resource: "Resource",
  link: "Link",
  quiz: "Quiz",
  assignment: "Assignment",
  video: "Video",
  other: "Resource",
};

export default function CourseDetail() {
  const { id } = useParams<{ id: string }>();
  const courseId = Number(id);

  const {
    data: course,
    loading: courseLoading,
    error: courseError,
    refetch: refetchCourse,
  } = useAsync(() => getCourseById(courseId), [courseId]);
  const {
    data: assignments,
    loading: assignmentsLoading,
    error: assignmentsError,
    refetch: refetchAssignments,
  } = useAsync(() => getAssignmentsByCourse(courseId), [courseId]);
  const {
    data: resources,
    loading: resourcesLoading,
    error: resourcesError,
    refetch: refetchResources,
  } = useAsync(() => getResourcesByCourse(courseId), [courseId]);

  if (courseLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (courseError) {
    return <ErrorState title="Couldn't load this course" message={courseError} onRetry={refetchCourse} />;
  }

  if (!course) {
    return (
      <EmptyState
        icon={ClipboardList}
        title="Course not found"
        description="This course doesn't exist or hasn't synced yet."
        action={
          <Link to="/courses" className="text-sm font-medium text-cyan-400 hover:text-cyan-300">
            Back to courses
          </Link>
        }
      />
    );
  }

  return (
    <div className="space-y-10">
      <div>
        <Link
          to="/courses"
          className="inline-flex items-center gap-1.5 text-sm text-graphite-400 transition-colors hover:text-warm-50"
        >
          <ArrowLeft size={15} /> Back to courses
        </Link>

        <div className="mt-4 flex items-start gap-4 rounded-2xl border border-graphite-700/60 bg-graphite-850/50 p-5">
          <div
            className="flex size-12 shrink-0 items-center justify-center rounded-xl font-display text-base font-semibold text-cyan-200"
            style={{
              background:
                "linear-gradient(135deg, color-mix(in oklab, var(--color-cyan-500) 22%, transparent), color-mix(in oklab, var(--color-violet-500) 18%, transparent))",
              border: "1px solid color-mix(in oklab, var(--color-cyan-400) 30%, transparent)",
            }}
          >
            {initialsFromName(course.name)}
          </div>
          <div className="min-w-0">
            <h2 className="font-display text-lg font-semibold text-warm-50">{course.name}</h2>
            {course.shortName && (
              <p className="text-xs font-medium uppercase tracking-wide text-graphite-400">{course.shortName}</p>
            )}
            {course.description && <p className="mt-2 text-sm text-graphite-300">{course.description}</p>}
          </div>
        </div>
      </div>

      <section>
        <h3 className="mb-3 font-display text-sm font-medium tracking-wide text-graphite-200">Assignments</h3>
        {assignmentsLoading ? (
          <ListSkeleton count={2} />
        ) : assignmentsError ? (
          <ErrorState message={assignmentsError} onRetry={refetchAssignments} />
        ) : assignments && assignments.length > 0 ? (
          <div className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 px-5">
            {assignments.map((a) => (
              <PriorityRow key={a.id} assignment={a} />
            ))}
          </div>
        ) : (
          <EmptyState icon={ClipboardList} title="No assignments" description="Nothing assigned for this course yet." />
        )}
      </section>

      <section>
        <h3 className="mb-3 font-display text-sm font-medium tracking-wide text-graphite-200">Resources</h3>
        {resourcesLoading ? (
          <ListSkeleton count={2} />
        ) : resourcesError ? (
          <ErrorState message={resourcesError} onRetry={refetchResources} />
        ) : resources && resources.length > 0 ? (
          <div className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 px-5">
            {resources.map((r) => (
              <div key={r.id} className="flex items-center gap-4 border-b border-graphite-700/50 py-3.5 last:border-b-0">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-graphite-700/70 bg-graphite-850 text-graphite-300">
                  <FileText size={16} strokeWidth={1.75} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-warm-50">{r.name}</p>
                  {r.description && <p className="truncate text-xs text-graphite-400">{r.description}</p>}
                </div>
                <span className="shrink-0 rounded-full border border-graphite-600/70 bg-graphite-800/60 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-graphite-300">
                  {resourceLabel[r.resourceType ?? "other"] ?? "Resource"}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState icon={FileText} title="No resources" description="No course materials synced for this course yet." />
        )}
      </section>
    </div>
  );
}
