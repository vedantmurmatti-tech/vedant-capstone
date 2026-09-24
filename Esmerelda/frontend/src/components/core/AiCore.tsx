import { useEffect, useId, useRef } from "react";
import { useReducedMotion } from "@/lib/useReducedMotion";
import { cn } from "@/lib/utils";

export type AiCoreState = "idle" | "active" | "processing";

interface AiCoreProps {
  size?: "sm" | "md" | "lg";
  state?: AiCoreState;
  className?: string;
  /**
   * A live (non-React-state) 0..1 speech-loudness reading — see
   * `useEsmereldaSpeech`'s `energyRef`. When provided, the core and glow
   * are additionally, directly modulated by its value every animation
   * frame via refs (not re-renders), layered on top of the existing
   * idle/active/processing look rather than replacing it. Omit it (as
   * `Dashboard.tsx` and every idle-state orb in `Chat.tsx` do) and this
   * component behaves exactly as it did before this prop existed.
   */
  energyRef?: React.RefObject<number>;
  /**
   * Optional finer-grained hint for what an "active"/"processing" state
   * actually represents, so visually-distinct real phases (listening vs
   * speaking; transcribing vs thinking) don't have to look identical.
   * Omit it (as every existing caller did before this prop existed —
   * `Dashboard.tsx`'s own idle-state orbs and every orb in `Chat.tsx`)
   * and `state="active"`/`"processing"` look exactly as they always have.
   */
  phase?: "listening" | "speaking" | "transcribing" | "thinking";
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
export function AiCore({ size = "lg", state = "idle", className, energyRef, phase }: AiCoreProps) {
  const reduceMotion = useReducedMotion();
  const uid = useId();
  const px = sizePx[size];
  const isBig = size === "lg";

  const outerGradientId = `core-outer-${uid}`;
  const coreGradientId = `core-center-${uid}`;

  // "processing" previously always used a single fixed timing (1.6s core
  // breathe / 4s ring rotation / 6s outer ring), for both transcribing and
  // thinking alike. Kept as the default here (so any caller that doesn't
  // pass `phase` — every existing one — sees pixel-identical durations to
  // before) while `phase` lets transcribing feel like a quick processing
  // burst and thinking feel slower and more deliberate, distinct from it.
  const coreBreatheDuration = state !== "processing" ? undefined : phase === "transcribing" ? "0.9s" : phase === "thinking" ? "2.6s" : "1.6s";
  const midRingDuration = state !== "processing" ? undefined : phase === "transcribing" ? "2s" : phase === "thinking" ? "5.5s" : "4s";
  const outerRingDuration = state !== "processing" ? undefined : phase === "transcribing" ? "3s" : phase === "thinking" ? "8s" : "6s";

  const glowRef = useRef<HTMLDivElement>(null);
  const coreRef = useRef<SVGCircleElement>(null);
  const ringRef = useRef<SVGCircleElement>(null);

  // Drives the core/glow/ring directly from `energyRef.current` every
  // frame via refs — not React state — so a real-time audio reading can
  // animate at display refresh rate without re-rendering this component
  // 60 times a second. Runs only while actually speaking; stops and
  // resets to the plain idle/processing look otherwise (satisfies "no
  // animation continues indefinitely after speech ends").
  useEffect(() => {
    if (!energyRef || reduceMotion || state !== "active") {
      // Not (or no longer) speech-reactive — release any inline overrides
      // so the ordinary CSS-driven idle/processing look takes back over.
      if (glowRef.current) {
        glowRef.current.style.opacity = "";
        glowRef.current.style.transform = "";
      }
      if (coreRef.current) coreRef.current.style.transform = "";
      if (ringRef.current) ringRef.current.style.strokeWidth = "";
      return;
    }

    let raf: number;
    const tick = () => {
      const energy = energyRef.current;

      // Organic breathing baseline (always present, even at ~0 energy) +
      // a real reaction that grows with loudness — quiet speech barely
      // moves it, a strong emphasis pushes it noticeably further out.
      const coreScale = 1 + energy * 0.32;
      const glowScale = 1 + energy * 0.22;
      const glowOpacity = 0.55 + energy * 0.4;
      const ringWidth = 0.6 + energy * 0.9;

      if (coreRef.current) coreRef.current.style.transform = `scale(${coreScale})`;
      if (glowRef.current) {
        glowRef.current.style.transform = `scale(${glowScale})`;
        glowRef.current.style.opacity = String(Math.min(1, glowOpacity));
      }
      if (ringRef.current) ringRef.current.style.strokeWidth = String(ringWidth);

      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => cancelAnimationFrame(raf);
  }, [energyRef, reduceMotion, state]);

  return (
    <div
      className={cn("relative inline-flex items-center justify-center", className)}
      style={{ width: px, height: px }}
      aria-hidden="true"
    >
      {/* ambient glow */}
      <div
        ref={glowRef}
        className={cn(
          "absolute inset-0 rounded-full blur-2xl transition-[opacity,transform] duration-150 ease-out",
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
          style={{ transformOrigin: "50px 50px", animationDuration: outerRingDuration }}
        />

        {/* middle solid ring with gaps, counter-rotating */}
        <circle
          ref={ringRef}
          cx="50"
          cy="50"
          r="37"
          fill="none"
          stroke="var(--color-cyan-400)"
          strokeWidth="0.8"
          strokeDasharray="40 18 6 18"
          opacity="0.5"
          className={cn(!reduceMotion && "animate-[var(--animate-orb-rotate-fast)] transition-[stroke-width] duration-150 ease-out")}
          style={{ transformOrigin: "50px 50px", animationDuration: midRingDuration }}
        />

        {/* inner fine ring — gets a slow, deliberate opacity pulse only
            during "thinking", distinguishing it from the quicker
            "transcribing" burst without adding a new element */}
        <circle
          cx="50"
          cy="50"
          r="29"
          fill="none"
          stroke="var(--color-graphite-300)"
          strokeWidth="0.3"
          opacity="0.35"
          className={cn(!reduceMotion && state === "processing" && phase === "thinking" && "animate-[var(--animate-thinking-pulse)]")}
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
          ref={coreRef}
          cx="50"
          cy="50"
          r="17"
          fill={`url(#${coreGradientId})`}
          className={cn(!reduceMotion && "animate-[var(--animate-core-breathe)] transition-transform duration-150 ease-out")}
          style={{ animationDuration: coreBreatheDuration, transformOrigin: "50px 50px" }}
        />
        <circle cx="50" cy="50" r="17" fill="none" stroke="var(--color-cyan-200)" strokeWidth="0.4" opacity="0.5" />
      </svg>
    </div>
  );
}
