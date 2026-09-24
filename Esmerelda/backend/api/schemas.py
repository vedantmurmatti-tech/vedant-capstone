"""Pydantic response models for the Esmerelda API.

Field names are camelCase to match the frontend's TypeScript types in
frontend/src/types/index.ts directly, with no field-name translation layer
needed in frontend/src/lib/api.ts.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CourseOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    moodleId: str
    name: str
    shortName: str | None
    description: str | None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    moodleId: str
    courseId: int
    courseName: str
    courseShortName: str | None
    name: str
    description: str | None
    dueDate: datetime | None
    submissionUrl: str | None
    submissionStatus: str | None = None


class ResourceOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    moodleId: str
    courseId: int
    courseName: str
    name: str
    resourceType: str | None
    url: str | None
    description: str | None


class DocumentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    resourceId: int | None
    courseId: int | None
    courseName: str | None
    name: str
    fileType: str | None
    updatedAt: datetime | None
    indexingStatus: str
    downloadUrl: str


class SyncStatusOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    state: str
    lastSyncedAt: datetime | None
    coursesTracked: int
    lastError: str | None = None


class SyncTriggerOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    runId: int
    state: str


class UserOut(BaseModel):
    """An Esmerelda user (see storage/models.py's User and api/auth.py's
    explicit dev-only identification mechanism — NOT production auth)."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    email: str
    displayName: str | None = None
    moodleSessionStatus: str | None = None


class MoodleSessionStatusOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["connected", "expired", "never_connected"]
    checkedAt: datetime | None = None


class ProactiveNotificationOut(BaseModel):
    """One concise, real, non-fabricated academic notice (see
    api/notifications.py) — always derived from actual stored Moodle data
    (Assignment.due_date, or a real growth in SyncRun's own already-stored
    per-run counts), never invented. `kind` is a small closed set the
    frontend can style by, not free-form text."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: Literal["due_soon", "new_content", "sync_error"]
    message: str
    courseId: int | None = None
    assignmentId: int | None = None


class DashboardSummaryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    coursesCount: int
    assignmentsCount: int
    resourcesCount: int
    documentsCount: int
    sync: SyncStatusOut
    # None when there's no earlier successful sync to compare against yet
    # (nothing fabricated in that case — just genuinely unknown); 0 or more
    # otherwise, computed from two real, already-stored SyncRun rows.
    newItemsCount: int | None = None
    notifications: list[ProactiveNotificationOut] = []


class ChatHistoryTurnIn(BaseModel):
    """One prior turn of the SAME logical conversation (see chat_agent.py's
    `handle_chat_message` — this is not a second reasoning/history system,
    just the shape a caller sends prior turns in, mirroring the
    role/content pairs already used for Groq/Gemini's own messages)."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequestIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Optional — an explicit label for the logical conversation this
    # message belongs to (e.g. one continuous voice session on the Orb).
    # Never used to look anything up server-side (this backend keeps no
    # server-side session state at all — see BUILD_LOG.md); it exists so
    # the caller has one, and so it can appear in logs for correlation.
    conversationId: str | None = Field(default=None, max_length=100)
    # Optional prior turns of the SAME conversation, oldest first. Capped
    # here defensively (independently of whatever the client already
    # trimmed to) so no single request can smuggle in unbounded context —
    # see chat_agent.py's own additional cap on top of this one.
    history: list[ChatHistoryTurnIn] = Field(default_factory=list, max_length=16)


class SpeechRequestIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class TranscriptionOut(BaseModel):
    text: str


class ChatSourceOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str
    courseName: str


class McpCallOut(BaseModel):
    """One real MCP tools/call invocation that actually happened during
    this chat turn — see backend/api/mcp_bridge.py. Only ever populated
    from LoggingSqliteSession.call_tool() having genuinely run; never
    fabricated, and correctly empty on turns that didn't need it."""

    model_config = ConfigDict(populate_by_name=True)

    server: str
    tool: str
    arguments: dict
    isError: bool


class ChatResponseOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reply: str
    assignments: list[AssignmentOut] = []
    courses: list[CourseOut] = []
    documents: list[DocumentOut] = []
    sources: list[ChatSourceOut] = []
    followUps: list[str] = []
    mcpCalls: list[McpCallOut] = []
    # Set by api/response_mode.py from the reasoning layer's own trailing
    # marker in its reply text — never taken from the model unvalidated.
    # Always exactly "voice" or "visual"; a missing/malformed decision from
    # the model always resolves to "voice" before it ever reaches here.
    responseMode: Literal["voice", "visual"] = "voice"
    # Only ever a small, fixed set of booleans (see build_visual_context())
    # — never arbitrary model output, never a route, never anything
    # executable. Present only when responseMode is "visual" AND there is
    # real, already tool-collected data to point at.
    visualContext: dict[str, bool] | None = None
