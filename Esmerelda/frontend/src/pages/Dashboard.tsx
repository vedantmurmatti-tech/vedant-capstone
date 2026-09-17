import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ClipboardList, BookOpen, Database, ArrowRight } from "lucide-react";
import { useAsync } from "@/lib/useAsync";
import { getAssignments, getCourses, getDashboardSummary, getDocuments, getStudentName } from "@/lib/api";
import { commandChips } from "@/lib/mockData";
import { AiCore } from "@/components/core/AiCore";
import { CommandInput } from "@/components/ui/CommandInput";
import { PriorityRow } from "@/components/ui/PriorityRow";
import { DocumentIndexRow } from "@/components/ui/DocumentIndexRow";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { ListSkeleton, Skeleton } from "@/components/ui/LoadingSkeleton";
import { getAssignmentUrgency, relativeTimeFromNow } from "@/lib/utils";

function useGreetingWord(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Still up";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

const today = new Date().toLocaleDateString("en-US", {
  weekday: "long",
  month: "long",
  day: "numeric",
});

export default function Dashboard() {
  const navigate = useNavigate();
  const [command, setCommand] = useState("");

  const { data: name } = useAsync(getStudentName, []);
  const { data: summary, error: summaryError, refetch: refetchSummary } = useAsync(getDashboardSummary, []);
  const {
    data: assignments,
    loading: assignmentsLoading,
    error: assignmentsError,
    refetch: refetchAssignments,
  } = useAsync(getAssignments, []);
  const {
    data: courses,
    loading: coursesLoading,
    error: coursesError,
    refetch: refetchCourses,
  } = useAsync(getCourses, []);
  const {
    data: documents,
    loading: documentsLoading,
    error: documentsError,
    refetch: refetchDocuments,
  } = useAsync(getDocuments, []);

  const priorities = (assignments ?? [])
    .filter((a) => getAssignmentUrgency(a.dueDate) !== "upcoming")
    .sort((a, b) => new Date(a.dueDate ?? 0).getTime() - new Date(b.dueDate ?? 0).getTime());

  const feed = useMemo(() => {
    const list = assignments ?? [];
    return [...list]
      .sort((a, b) => new Date(a.dueDate ?? 0).getTime() - new Date(b.dueDate ?? 0).getTime())
      .slice(0, 4);
  }, [assignments]);

  const recentDocuments = useMemo(() => {
    return (documents ?? [])
      .slice()
      .sort((a, b) => new Date(b.updatedAt ?? 0).getTime() - new Date(a.updatedAt ?? 0).getTime())
      .slice(0, 4);
  }, [documents]);

  function submitCommand() {
    if (!command.trim()) return;
    navigate("/chat", { state: { initialMessage: command } });
  }

  const greeting = useGreetingWord();

  return (
    <div className="space-y-12">
      {/* Hero — AI presence */}
      <section className="relative flex flex-col items-center overflow-hidden rounded-3xl border border-graphite-700/50 px-6 py-14 text-center sm:py-16">
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(60% 60% at 50% 0%, color-mix(in oklab, var(--color-cyan-500) 10%, transparent), transparent 70%)",
          }}
          aria-hidden="true"
        />

        <p className="relative font-mono text-xs uppercase tracking-[0.2em] text-graphite-400">{today}</p>

        <div className="relative mt-6">
          <AiCore size="lg" state="idle" />
        </div>

        <h2 className="relative mt-6 font-display text-3xl font-semibold tracking-tight text-warm-50 sm:text-4xl">
          {greeting}, {name ?? "—"}.
        </h2>
        <p className="relative mt-2 text-sm text-graphite-300 sm:text-base">
          Your academic workspace is ready.
          {assignments && (
            <>
              {" "}
              <span className="text-cyan-300">
                {priorities.length} {priorities.length === 1 ? "priority requires" : "priorities require"} your
                attention.
              </span>
            </>
          )}
        </p>

        <div className="relative mt-3 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-xs text-graphite-500">
          {summaryError ? (
            <span className="text-status-critical">
              Couldn't reach the backend for system status.{" "}
              <button type="button" onClick={refetchSummary} className="underline hover:text-warm-50">
                Retry
              </button>
            </span>
          ) : summary ? (
            <>
              <span>{summary.coursesCount} active courses</span>
              <span className="hidden text-graphite-700 sm:inline">•</span>
              <span>{summary.documentsCount} documents indexed</span>
              <span className="hidden text-graphite-700 sm:inline">•</span>
              <span>
                {summary.sync.lastSyncedAt
                  ? `Synced ${relativeTimeFromNow(summary.sync.lastSyncedAt)}`
                  : "Not synced yet"}
              </span>
            </>
          ) : (
            <span>Checking system status…</span>
          )}
        </div>

        <div className="relative mt-10 w-full max-w-2xl">
          <CommandInput
            value={command}
            onChange={setCommand}
            onSubmit={submitCommand}
            placeholder={`How can I help you today, ${name ?? "there"}?`}
            variant="console"
          />
          <div className="mt-8 flex flex-wrap justify-center gap-2">
            {commandChips.map((chip) => (
              <button
                key={chip}
                type="button"
                onClick={() => navigate("/chat", { state: { initialMessage: chip } })}
                className="rounded-full border border-graphite-600/60 bg-graphite-850/60 px-3.5 py-1.5 text-xs text-graphite-300 transition-all hover:border-cyan-500/50 hover:text-cyan-200"
              >
                {chip}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Priority feed */}
      <section>
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ClipboardList size={15} className="text-graphite-500" />
            <h3 className="font-display text-sm font-medium tracking-wide text-graphite-200">Priority feed</h3>
          </div>
          <Link to="/assignments" className="flex items-center gap-1 text-xs font-medium text-cyan-400 hover:text-cyan-300">
            View all <ArrowRight size={13} />
          </Link>
        </div>
        {assignmentsLoading ? (
          <ListSkeleton count={4} />
        ) : assignmentsError ? (
          <ErrorState message={assignmentsError} onRetry={refetchAssignments} />
        ) : feed.length > 0 ? (
          <div className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 px-5">
            {feed.map((a) => (
              <PriorityRow key={a.id} assignment={a} />
            ))}
          </div>
        ) : (
          <EmptyState icon={ClipboardList} title="Nothing due soon" description="You're all caught up on assignments." />
        )}
      </section>

      <div className="grid grid-cols-1 gap-10 lg:grid-cols-2">
        {/* Active courses */}
        <section>
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BookOpen size={15} className="text-graphite-500" />
              <h3 className="font-display text-sm font-medium tracking-wide text-graphite-200">Active courses</h3>
            </div>
            <Link to="/courses" className="flex items-center gap-1 text-xs font-medium text-cyan-400 hover:text-cyan-300">
              View all <ArrowRight size={13} />
            </Link>
          </div>
          {coursesLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-full rounded-xl" />
              <Skeleton className="h-16 w-full rounded-xl" />
            </div>
          ) : coursesError ? (
            <ErrorState message={coursesError} onRetry={refetchCourses} />
          ) : courses && courses.length > 0 ? (
            <div className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 px-5">
              {courses.map((c) => (
                <Link
                  key={c.id}
                  to={`/courses/${c.id}`}
                  className="group flex items-center justify-between border-b border-graphite-700/50 py-3.5 last:border-b-0"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-warm-50">{c.name}</p>
                    <p className="text-xs text-graphite-400">{c.shortName}</p>
                  </div>
                  <ArrowRight size={14} className="shrink-0 text-graphite-500 transition-transform group-hover:translate-x-0.5 group-hover:text-cyan-400" />
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState icon={BookOpen} title="No courses yet" description="Courses will appear once Moodle sync runs." />
          )}
        </section>

        {/* Recently indexed documents */}
        <section>
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Database size={15} className="text-graphite-500" />
              <h3 className="font-display text-sm font-medium tracking-wide text-graphite-200">Recently indexed</h3>
            </div>
            <Link to="/documents" className="flex items-center gap-1 text-xs font-medium text-cyan-400 hover:text-cyan-300">
              View all <ArrowRight size={13} />
            </Link>
          </div>
          {documentsLoading ? (
            <ListSkeleton count={4} />
          ) : documentsError ? (
            <ErrorState message={documentsError} onRetry={refetchDocuments} />
          ) : recentDocuments.length > 0 ? (
            <div className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 px-5">
              {recentDocuments.map((d) => (
                <DocumentIndexRow key={d.id} document={d} />
              ))}
            </div>
          ) : (
            <EmptyState icon={Database} title="No documents yet" description="Indexed files will show up here." />
          )}
        </section>
      </div>
    </div>
  );
}
