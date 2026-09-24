/**
 * API service layer — talks to the real FastAPI backend (backend/api/routes.py,
 * mounted from backend/main.py). Every function here does exactly one
 * `apiFetch` call; pages only ever import from this module, never touch
 * `fetch` directly, and never fall back to mock data on failure.
 *
 * Base URL comes from `VITE_API_BASE_URL` (see frontend/.env), so it can
 * point at a different host without a code change.
 *
 * `/api/chat` is a real, rule-based agent over the same database (see
 * backend/api/chat_agent.py) — not a generative model. `sendChatMessage`
 * only ever throws `ChatUnavailableError` when the backend itself can't be
 * reached, never as a stand-in for "no AI exists" anymore.
 */

import type {
  Assignment,
  ChatApiResponse,
  ChatHistoryTurn,
  Course,
  DashboardSummary,
  DocumentFile,
  MoodleSyncStatus,
  Resource,
  SyncTriggerResponse,
} from "@/types";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

// Multi-user foundation (see BUILD_LOG.md) — this is a DEV-ONLY identity
// mechanism (see backend/api/auth.py's own docstring for exactly what it
// does and doesn't guarantee), not real authentication. A plain,
// per-browser localStorage value that every apiFetch call sends as
// X-Esmerelda-User-Id; the backend creates the user the first time a
// given id is seen. No selection UI exists yet beyond
// getCurrentUserId()/setCurrentUserId() below — omitted, the backend
// falls back to one shared demo user, exactly matching every pre-
// multi-user request this app already made.
const USER_ID_STORAGE_KEY = "esmerelda_user_id";

export function getCurrentUserId(): string | null {
  try {
    return localStorage.getItem(USER_ID_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setCurrentUserId(userId: string | number): void {
  try {
    localStorage.setItem(USER_ID_STORAGE_KEY, String(userId));
  } catch {
    // localStorage unavailable (private browsing, blocked site data) —
    // requests simply fall back to the shared demo user server-side.
  }
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  const userId = getCurrentUserId();
  try {
    res = await fetch(`${API_BASE}/api${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(userId ? { "X-Esmerelda-User-Id": userId } : {}),
      },
      ...init,
    });
  } catch {
    throw new ApiError(
      `Couldn't reach Esmerelda's backend at ${API_BASE}. Is it running?`,
      0
    );
  }

  if (!res.ok) {
    // Prefer the backend's own real detail message (every error response
    // this API returns is `{"detail": "..."}` — see routes.py's
    // HTTPException usages) over a generic "status N" — the same pattern
    // `transcribeAudio()`/`synthesizeSpeech()` already use their own
    // fetch calls for. Falls back to the generic message if the body
    // isn't JSON, or has no `detail`, so this never throws a *worse*
    // error than before this fix.
    let message = `Request to ${path} failed with status ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) message = body.detail;
    } catch {
      // not JSON, or empty body — keep the generic status-based message
    }
    throw new ApiError(message, res.status);
  }
  return res.json() as Promise<T>;
}

export async function getCourses(): Promise<Course[]> {
  return apiFetch<Course[]>("/courses");
}

export async function getCourseById(id: number): Promise<Course | undefined> {
  try {
    return await apiFetch<Course>(`/courses/${id}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return undefined;
    throw err;
  }
}

export async function getAssignments(): Promise<Assignment[]> {
  return apiFetch<Assignment[]>("/assignments");
}

export async function getAssignmentsByCourse(courseId: number): Promise<Assignment[]> {
  return apiFetch<Assignment[]>(`/courses/${courseId}/assignments`);
}

export async function getResourcesByCourse(courseId: number): Promise<Resource[]> {
  return apiFetch<Resource[]>(`/courses/${courseId}/resources`);
}

export async function getDocuments(): Promise<DocumentFile[]> {
  return apiFetch<DocumentFile[]>("/documents");
}

export function getDocumentDownloadUrl(document: DocumentFile): string {
  // A plain <a href> link, not an apiFetch call — can't carry the
  // X-Esmerelda-User-Id header, so the user id is appended as
  // ?userId= instead (backend/api/auth.py's get_current_user_id()
  // accepts either).
  const userId = getCurrentUserId();
  const base = `${API_BASE}${document.downloadUrl}`;
  return userId ? `${base}?userId=${encodeURIComponent(userId)}` : base;
}

export async function getCurrentUser(): Promise<{
  id: number;
  email: string;
  displayName: string | null;
  moodleSessionStatus: string | null;
}> {
  return apiFetch("/users/me");
}

export async function createUser(
  email: string,
  displayName?: string
): Promise<{ id: number; email: string; displayName: string | null; moodleSessionStatus: string | null }> {
  const params = new URLSearchParams({ email });
  if (displayName) params.set("display_name", displayName);
  return apiFetch(`/users?${params.toString()}`, { method: "POST" });
}

export async function getMoodleSessionStatus(): Promise<{
  status: "connected" | "expired" | "never_connected";
  checkedAt: string | null;
}> {
  return apiFetch("/moodle/session-status");
}

export async function connectMoodle(): Promise<{
  status: "connected" | "expired" | "never_connected";
  checkedAt: string | null;
}> {
  return apiFetch("/moodle/connect", { method: "POST" });
}

export async function disconnectMoodle(): Promise<{
  status: "connected" | "expired" | "never_connected";
  checkedAt: string | null;
}> {
  return apiFetch("/moodle/disconnect", { method: "POST" });
}

export async function getMoodleSyncStatus(): Promise<MoodleSyncStatus> {
  return apiFetch<MoodleSyncStatus>("/sync-status");
}

export async function triggerMoodleSync(): Promise<SyncTriggerResponse> {
  return apiFetch<SyncTriggerResponse>("/sync/moodle", { method: "POST" });
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  return apiFetch<DashboardSummary>("/dashboard/summary");
}

// No user/profile backend exists — this is a static UI label, not domain
// data, so it isn't routed through apiFetch or presented as synced data.
export async function getStudentName(): Promise<string> {
  return "Vedant";
}

export class SpeechUnavailableError extends Error {
  constructor(reason: string) {
    super(`Esmerelda's voice isn't available right now — ${reason}`);
    this.name = "SpeechUnavailableError";
  }
}

/**
 * Sends text to POST /api/speech and returns a playable object URL for the
 * generated audio (see backend/api/tts.py). Caller is responsible for
 * revoking the URL (`URL.revokeObjectURL`) once done with it.
 */
export async function synthesizeSpeech(text: string): Promise<string> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/speech`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch {
    throw new SpeechUnavailableError(`couldn't reach ${API_BASE}.`);
  }

  if (!res.ok) {
    throw new SpeechUnavailableError(`request failed with status ${res.status}.`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export class TranscriptionUnavailableError extends Error {
  constructor(reason: string) {
    super(`Esmerelda couldn't understand that recording — ${reason}`);
    this.name = "TranscriptionUnavailableError";
  }
}

/**
 * Sends a recorded audio Blob to POST /api/transcribe (see
 * backend/api/stt.py) and returns the recognized text. Only ever called
 * with a Blob produced by `useVoiceInput` — never sends anything to the
 * chat agent or navigates anywhere; that wiring is a later step.
 */
export async function transcribeAudio(blob: Blob): Promise<string> {
  const form = new FormData();
  form.append("audio", blob, "recording.webm");

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/transcribe`, {
      method: "POST",
      body: form,
    });
  } catch {
    throw new TranscriptionUnavailableError(`couldn't reach ${API_BASE}.`);
  }

  if (!res.ok) {
    let detail = `request failed with status ${res.status}.`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // Response body wasn't JSON — fall back to the generic status message above.
    }
    throw new TranscriptionUnavailableError(detail);
  }

  const data = (await res.json()) as { text?: string };
  return typeof data.text === "string" ? data.text : "";
}

export class ChatUnavailableError extends Error {
  constructor(reason: string) {
    super(`Esmerelda's AI backend isn't reachable right now — ${reason}`);
    this.name = "ChatUnavailableError";
  }
}

/**
 * Sends a message to the real chat agent at POST /api/chat (a rule-based
 * agent over the live database — see backend/api/chat_agent.py — not a
 * generative model). Throws `ChatUnavailableError` only when the backend
 * itself can't be reached or errors, so the UI can show an honest
 * "unavailable" state rather than ever fabricating a reply.
 *
 * `options.history`/`options.conversationId` are both optional — every
 * existing call site (Chat.tsx) omits them and sends exactly the same
 * request body it always has (`history` defaults to `[]` server-side,
 * which is byte-for-byte what an omitted history field already meant).
 * They exist so a caller maintaining its own multi-turn context (the Orb's
 * voice loop — see Dashboard.tsx) can pass prior turns of the SAME
 * conversation through the one existing reasoning pipeline, without a
 * second implementation of it.
 */
export async function sendChatMessage(
  message: string,
  options?: { conversationId?: string; history?: ChatHistoryTurn[] }
): Promise<ChatApiResponse> {
  try {
    return await apiFetch<ChatApiResponse>("/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        conversationId: options?.conversationId,
        history: options?.history ?? [],
      }),
    });
  } catch (err) {
    if (err instanceof ApiError) {
      throw new ChatUnavailableError(err.message);
    }
    throw err;
  }
}
