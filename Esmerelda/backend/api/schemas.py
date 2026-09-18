"""Pydantic response models for the Esmerelda API.

Field names are camelCase to match the frontend's TypeScript types in
frontend/src/types/index.ts directly, with no field-name translation layer
needed in frontend/src/lib/api.ts.
"""

from datetime import datetime
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


class DashboardSummaryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    coursesCount: int
    assignmentsCount: int
    documentsCount: int
    sync: SyncStatusOut


class ChatRequestIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


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
