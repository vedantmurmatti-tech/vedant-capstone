import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("shimmer-surface rounded-lg", className)} />;
}

export function CardSkeleton() {
  return (
    <div className="rounded-2xl border border-graphite-700/60 bg-graphite-850/50 p-5">
      <div className="flex items-start justify-between gap-3">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-5 w-14 shrink-0" />
      </div>
      <Skeleton className="mt-3 h-3 w-1/2" />
      <Skeleton className="mt-6 h-3 w-full" />
      <Skeleton className="mt-2 h-3 w-4/5" />
    </div>
  );
}

export function CardSkeletonGrid({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: count }).map((_, i) => (
        <CardSkeleton key={i} />
      ))}
    </div>
  );
}

export function RowSkeleton() {
  return (
    <div className="flex items-center gap-4 border-b border-graphite-700/50 py-3.5 last:border-b-0">
      <Skeleton className="size-9 shrink-0 rounded-lg" />
      <div className="flex-1 space-y-2">
        <Skeleton className="h-3.5 w-1/3" />
        <Skeleton className="h-3 w-1/4" />
      </div>
      <Skeleton className="h-4 w-16 shrink-0 rounded-full" />
    </div>
  );
}

export function ListSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 px-4">
      {Array.from({ length: count }).map((_, i) => (
        <RowSkeleton key={i} />
      ))}
    </div>
  );
}
