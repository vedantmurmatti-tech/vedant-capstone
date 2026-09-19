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

export interface DashboardSummary {
  coursesCount: number;
  assignmentsCount: number;
  documentsCount: number;
  sync: MoodleSyncStatus;
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

// POST /api/chat response shape (backend/api/schemas.py ChatResponseOut).
export interface ChatApiResponse {
  reply: string;
  assignments: Assignment[];
  courses: Course[];
  documents: DocumentFile[];
  sources: ChatSource[];
  followUps: string[];
}
