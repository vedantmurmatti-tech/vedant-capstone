import { useId } from "react";
import { useReducedMotion } from "@/lib/useReducedMotion";
import { cn } from "@/lib/utils";

export type AiCoreState = "idle" | "active" | "processing";

interface AiCoreProps {
  size?: "sm" | "md" | "lg";
  state?: AiCoreState;
  className?: string;
}

const sizePx: Record<NonNullable<AiCoreProps["size"]>, number> = {
  sm: 40,
  md: 88,
  lg: 220,
};

/**
 * Esmerelda's animated presence — a layered SVG reactor core with
 * counter-rotating rings and a breathing gradient center. Purely
 * decorative/ambient; state changes (idle/active/processing) only
 * affect animation speed and glow intensity, never blocking content.
 */
export function AiCore({ size = "lg", state = "idle", className }: AiCoreProps) {
  const reduceMotion = useReducedMotion();
  const uid = useId();
  const px = sizePx[size];
  const isBig = size === "lg";

  const outerGradientId = `core-outer-${uid}`;
  const coreGradientId = `core-center-${uid}`;

  return (
    <div
      className={cn("relative inline-flex items-center justify-center", className)}
      style={{ width: px, height: px }}
      aria-hidden="true"
    >
      {/* ambient glow */}
      <div
        className={cn(
          "absolute inset-0 rounded-full blur-2xl transition-opacity duration-700",
          state === "processing" ? "opacity-90" : state === "active" ? "opacity-70" : "opacity-45"
        )}
        style={{
          background:
            "radial-gradient(circle, color-mix(in oklab, var(--color-cyan-400) 60%, transparent) 0%, transparent 70%)",
        }}
      />

      <svg
        viewBox="0 0 100 100"
        width={px}
        height={px}
        className="relative"
        style={{ filter: isBig ? "drop-shadow(0 0 18px color-mix(in oklab, var(--color-cyan-500) 45%, transparent))" : undefined }}
      >
        <defs>
          <linearGradient id={outerGradientId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="var(--color-cyan-400)" />
            <stop offset="100%" stopColor="var(--color-violet-500)" />
          </linearGradient>
          <radialGradient id={coreGradientId} cx="50%" cy="45%" r="60%">
            <stop offset="0%" stopColor="var(--color-cyan-300)" />
            <stop offset="55%" stopColor="var(--color-cyan-500)" />
            <stop offset="100%" stopColor="var(--color-violet-600)" />
          </radialGradient>
        </defs>

        {/* outer dashed ring */}
        <circle
          cx="50"
          cy="50"
          r="46"
          fill="none"
          stroke={`url(#${outerGradientId})`}
          strokeWidth="0.6"
          strokeDasharray="2 5"
          opacity="0.55"
          className={cn(!reduceMotion && "origin-center", !reduceMotion && "animate-[var(--animate-orb-rotate-slow)]")}
          style={{ transformOrigin: "50px 50px", animationDuration: state === "processing" ? "6s" : undefined }}
        />

        {/* middle solid ring with gaps, counter-rotating */}
        <circle
          cx="50"
          cy="50"
          r="37"
          fill="none"
          stroke="var(--color-cyan-400)"
          strokeWidth="0.8"
          strokeDasharray="40 18 6 18"
          opacity="0.5"
          className={cn(!reduceMotion && "animate-[var(--animate-orb-rotate-fast)]")}
          style={{ transformOrigin: "50px 50px", animationDuration: state === "processing" ? "4s" : undefined }}
        />

        {/* inner fine ring */}
        <circle
          cx="50"
          cy="50"
          r="29"
          fill="none"
          stroke="var(--color-graphite-300)"
          strokeWidth="0.3"
          opacity="0.35"
        />

        {/* orbiting particles, riding the outer ring's rotation */}
        <g
          className={cn(!reduceMotion && "animate-[var(--animate-orb-rotate-slow)]")}
          style={{ transformOrigin: "50px 50px" }}
        >
          <circle cx="50" cy="4" r="1.4" fill="var(--color-cyan-300)" />
          <circle cx="50" cy="96" r="1" fill="var(--color-violet-400)" opacity="0.8" />
        </g>

        {/* breathing core */}
        <circle
          cx="50"
          cy="50"
          r="17"
          fill={`url(#${coreGradientId})`}
          className={cn(!reduceMotion && "animate-[var(--animate-core-breathe)]")}
          style={{ animationDuration: state === "processing" ? "1.6s" : undefined }}
        />
        <circle cx="50" cy="50" r="17" fill="none" stroke="var(--color-cyan-200)" strokeWidth="0.4" opacity="0.5" />
      </svg>
    </div>
  );
}
