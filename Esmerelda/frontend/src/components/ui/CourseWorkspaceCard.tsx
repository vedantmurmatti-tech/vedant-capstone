import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import type { Course } from "@/types";
import { initialsFromName } from "@/lib/utils";

interface CourseWorkspaceCardProps {
  course: Course;
  assignmentCount: number;
}

export function CourseWorkspaceCard({ course, assignmentCount }: CourseWorkspaceCardProps) {
  return (
    <Link
      to={`/courses/${course.id}`}
      className="group relative flex flex-col overflow-hidden rounded-2xl border border-graphite-700/60 bg-graphite-850/50 p-5 transition-all duration-300 hover:border-cyan-500/40 hover:bg-graphite-850"
    >
      <div
        className="pointer-events-none absolute -right-10 -top-10 size-32 rounded-full opacity-0 blur-2xl transition-opacity duration-500 group-hover:opacity-100"
        style={{ background: "radial-gradient(circle, color-mix(in oklab, var(--color-cyan-500) 35%, transparent), transparent 70%)" }}
        aria-hidden="true"
      />

      <div className="relative flex items-start justify-between gap-3">
        <div
          className="flex size-10 shrink-0 items-center justify-center rounded-xl font-display text-sm font-semibold text-cyan-200"
          style={{
            background: "linear-gradient(135deg, color-mix(in oklab, var(--color-cyan-500) 22%, transparent), color-mix(in oklab, var(--color-violet-500) 18%, transparent))",
            border: "1px solid color-mix(in oklab, var(--color-cyan-400) 30%, transparent)",
          }}
        >
          {initialsFromName(course.name)}
        </div>
        <ArrowUpRight
          size={16}
          className="mt-2 shrink-0 text-graphite-500 transition-all group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-cyan-400"
        />
      </div>

      <h3 className="relative mt-4 font-display text-[15px] font-medium leading-snug text-warm-50">
        {course.name}
      </h3>
      {course.shortName && (
        <p className="relative mt-1 text-xs font-medium uppercase tracking-wide text-graphite-400">
          {course.shortName}
        </p>
      )}

      {course.description && (
        <p className="relative mt-3 line-clamp-2 flex-1 text-sm text-graphite-400">{course.description}</p>
      )}

      <div className="relative mt-4 flex items-center gap-1.5 border-t border-graphite-700/50 pt-3 text-xs text-graphite-400">
        <span className="size-1.5 rounded-full bg-cyan-400/70" />
        <span>
          {assignmentCount} active {assignmentCount === 1 ? "assignment" : "assignments"}
        </span>
      </div>
    </Link>
  );
}
