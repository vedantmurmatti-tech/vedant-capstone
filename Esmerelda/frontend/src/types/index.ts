// Mirrors backend/api/schemas.py response shapes (which in turn mirror
// backend/storage/models.py), field-for-field, so no translation layer is
// needed between the API response and these types.

export interface Course {
  id: number;
  moodleId: string;
  name: string;
  shortName: string | null;
  description: string | null;
}

export interface Assignment {
  id: number;
  moodleId: string;
  courseId: number;
  courseName: string;
  courseShortName: string | null;
  name: string;
  description: string | null;
  dueDate: string | null; // ISO 8601
  submissionUrl: string | null;
  // Best-effort — scraped from Moodle's own assignment submission-status
  // table by backend/moodle/sync_service.py's _fetch_submission_status();
  // null when it couldn't be determined (a different theme/layout, a
  // network hiccup fetching that one page) rather than a real "no status".
  submissionStatus: string | null;
}

// The backend derives this from Moodle's raw activity/resource type
// (see backend/storage/models.py Resource.resource_type) rather than
// classifying file types — those are only known once a resource has an
// associated downloaded Document.
export type ResourceType = "activity" | "resource" | "link" | "quiz" | "assignment" | "video" | "other";

export interface Resource {
  id: number;
  moodleId: string;
  courseId: number;
  courseName: string;
  name: string;
  resourceType: ResourceType | null;
  url: string | null;
  description: string | null;
}

// "indexed" is the only real state right now — every row in the `documents`
// table was, by definition, already downloaded successfully. "indexing" /
// "queued" are reserved for when a real indexing pipeline exists.
export type IndexingStatus = "indexed" | "indexing" | "queued";

export interface DocumentFile {
  id: number;
  resourceId: number | null;
  courseId: number | null;
  courseName: string | null;
  name: string;
  fileType: string | null;
  updatedAt: string | null; // ISO 8601, from latest DocumentVersion
  indexingStatus: IndexingStatus;
  downloadUrl: string;
}

export type MoodleSyncState = "synced" | "syncing" | "stale" | "error";

export interface MoodleSyncStatus {
  state: MoodleSyncState;
  lastSyncedAt: string | null; // ISO 8601
  coursesTracked: number;
  message?: string;
  lastError?: string | null;
}

export interface SyncTriggerResponse {
  runId: number;
  state: MoodleSyncState;
}

// One concise, real, non-fabricated academic notice (backend/api/notifications.py
// ProactiveNotificationOut) — always derived from actual stored Moodle
// data. `kind` is a small closed set to style by, not free-form text.
export type ProactiveNotificationKind = "due_soon" | "new_content" | "sync_error";

export interface ProactiveNotification {
  id: string;
  kind: ProactiveNotificationKind;
  message: string;
  courseId: number | null;
  assignmentId: number | null;
}

export interface DashboardSummary {
  coursesCount: number;
  assignmentsCount: number;
  resourcesCount: number;
  documentsCount: number;
  sync: MoodleSyncStatus;
  // null when there's no earlier successful sync to compare against yet
  // (genuinely unknown, never reported as 0).
  newItemsCount: number | null;
  notifications: ProactiveNotification[];
}

export type ChatRole = "user" | "assistant";

export interface ChatSource {
  label: string;
  courseName: string;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: string;
  sources?: ChatSource[];
  followUps?: string[];
  assignments?: Assignment[];
  courses?: Course[];
  documents?: DocumentFile[];
  /** True when this message is a system notice that the AI backend has no endpoint to answer from, not a real answer. */
  unavailable?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  updatedAt: string;
}

// One prior turn of the same logical conversation, sent back to POST
// /api/chat (backend/api/schemas.py ChatHistoryTurnIn) so the reasoning
// pipeline can resolve follow-ups like "which one" / "that". Optional —
// omitting it (as every call before this existed did) behaves exactly as
// before.
export interface ChatHistoryTurn {
  role: ChatRole;
  content: string;
}

// Set server-side (backend/api/response_mode.py) from the reasoning
// layer's own trailing marker in its reply — always exactly one of these
// two values, never taken from the model unvalidated. "voice" is always
// the safe default.
export type ResponseMode = "voice" | "visual";

// Only ever a small, fixed set of booleans (see backend's
// build_visual_context()) — never arbitrary model output or a route.
export interface VisualContext {
  hasAssignments: boolean;
  hasCourses: boolean;
  hasDocuments: boolean;
}

// POST /api/chat response shape (backend/api/schemas.py ChatResponseOut).
export interface ChatApiResponse {
  reply: string;
  assignments: Assignment[];
  courses: Course[];
  documents: DocumentFile[];
  sources: ChatSource[];
  followUps: string[];
  responseMode: ResponseMode;
  visualContext: VisualContext | null;
}
