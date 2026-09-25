import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export function formatDate(iso: string | null, opts?: Intl.DateTimeFormatOptions): string {
  if (!iso) return "No date";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "No date";
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    ...opts,
  });
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "Unknown";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function relativeTimeFromNow(iso: string | null): string {
  if (!iso) return "unknown";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown";

  const diffMs = date.getTime() - Date.now();
  const diffMinutes = Math.round(diffMs / 60000);
  const diffHours = Math.round(diffMs / 3_600_000);
  const diffDays = Math.round(diffMs / 86_400_000);

  const abs = Math.abs(diffDays);
  if (abs >= 1) {
    const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
    return rtf.format(diffDays, "day");
  }
  if (Math.abs(diffHours) >= 1) {
    const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
    return rtf.format(diffHours, "hour");
  }
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  return rtf.format(diffMinutes, "minute");
}

export function getAssignmentUrgency(dueDate: string | null): "overdue" | "due-soon" | "upcoming" | "none" {
  if (!dueDate) return "none";
  const due = new Date(dueDate).getTime();
  if (Number.isNaN(due)) return "none";
  const now = Date.now();
  const diffHours = (due - now) / 3_600_000;

  if (diffHours < 0) return "overdue";
  if (diffHours <= 48) return "due-soon";
  return "upcoming";
}

/**
 * Strips the small set of markdown syntax ChatMarkdown.tsx actually
 * renders (fenced/inline code, bold/italics, headings, list markers,
 * links) down to plain, speakable text for the TTS endpoint. Not a full
 * markdown parser — just enough to avoid literally reading out `**`,
 * backticks, `#`, `- `, or `[label](url)` syntax aloud.
 */
export function stripMarkdownForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^[-*]\s+/gm, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[_~]/g, "")
    .replace(/\n{2,}/g, ". ")
    .replace(/\n/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/**
 * Voice-output presentation rule (see BUILD_LOG.md): Moodle course names,
 * as scraped and stored, carry their course code/section/term baked
 * directly into the string — e.g. "DESG319-UGSEM5-2026/27S1-Introduction
 * to Artificial Intelligence & Machine Learning" — because that IS
 * `Course.name` (there is no separate "just the title" column). Reading
 * that whole string aloud through TTS ("DESG three one nine, 2026 slash
 * 27 S1, Introduction to...") adds real cognitive load for no reason in
 * normal spoken responses, even though the code/term remain exactly as
 * useful as ever for someone reading the screen.
 *
 * This function is applied ONLY at the TTS boundary (useEsmereldaSpeech's
 * speak(), right below) — never to what ChatMarkdown.tsx renders, never
 * to the underlying `courseName`/`courseShortName` values the API
 * returns, and never to the database. It only ever strips a LEADING
 * code/section/term prefix when real title text follows it in the same
 * spoken string — a bare course code with nothing after it (the one case
 * where stripping could leave a broken sentence with no noun at all) is
 * left untouched, since there's nothing safe to replace it with here.
 */
export function simplifyMoodleCourseIdentifiersForSpeech(text: string): string {
  return text
    // Raw Moodle course name shape: CODE-SECTION-TERM-Title (hyphen-joined,
    // e.g. "DESG319-UGSEM5-2026/27S1-Introduction to AI & ML") -> "Introduction to AI & ML".
    .replace(/\b[A-Z]{2,6}\d{2,4}-[A-Za-z0-9]+-\d{4}\/\d{2}S\d-(?=\S)/g, "")
    // Derived short display name shape: "CODE · TERM" (e.g. "DESG319 · 2026/27S1"),
    // optionally followed by a separator and the real title -> just the title.
    .replace(/\b[A-Z]{2,6}\d{2,4}\s*·\s*\d{4}\/\d{2}S\d\s*[—\-:]\s*(?=\S)/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

export function initialsFromName(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
