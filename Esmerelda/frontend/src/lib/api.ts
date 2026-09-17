/**
 * API service layer — talks to the real FastAPI backend (backend/api/routes.py,
 * mounted from backend/venv/main.py). Every function here does exactly one
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
  Course,
  DashboardSummary,
  DocumentFile,
  MoodleSyncStatus,
  Resource,
} from "@/types";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

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
  try {
    res = await fetch(`${API_BASE}/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(
      `Couldn't reach Esmerelda's backend at ${API_BASE}. Is it running?`,
      0
    );
  }

  if (!res.ok) {
    throw new ApiError(`Request to ${path} failed with status ${res.status}`, res.status);
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
  return `${API_BASE}${document.downloadUrl}`;
}

export async function getMoodleSyncStatus(): Promise<MoodleSyncStatus> {
  return apiFetch<MoodleSyncStatus>("/sync-status");
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  return apiFetch<DashboardSummary>("/dashboard/summary");
}

// No user/profile backend exists — this is a static UI label, not domain
// data, so it isn't routed through apiFetch or presented as synced data.
export async function getStudentName(): Promise<string> {
  return "Vedant";
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
 */
export async function sendChatMessage(message: string): Promise<ChatApiResponse> {
  try {
    return await apiFetch<ChatApiResponse>("/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
  } catch (err) {
    if (err instanceof ApiError) {
      throw new ChatUnavailableError(err.message);
    }
    throw err;
  }
}
