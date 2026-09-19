from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, DateTime
from datetime import datetime

from .database import Base


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    moodle_id: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    short_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)

class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    moodle_id: Mapped[str] = mapped_column(String(255), unique=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    submission_url: Mapped[str | None] = mapped_column(String(500))
    submission_status: Mapped[str | None] = mapped_column(String(255))

class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    moodle_id: Mapped[str] = mapped_column(String(255), unique=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))

    name: Mapped[str] = mapped_column(String(255))
    resource_type: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(Text)

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("resources.id"),
        nullable=True
    )

    name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(1000))
    file_type: Mapped[str | None] = mapped_column(String(100))
    current_hash: Mapped[str | None] = mapped_column(String(64))
    # Plain extracted text (PDF/DOCX/PPTX — see storage/text_extraction.py),
    # populated once at download time by moodle/document_downloader.py.
    # This IS the Knowledge Base's indexed representation — chat-time
    # retrieval (api/document_retrieval.py) searches this column directly
    # rather than ever re-reading files from disk per chat request. NULL
    # for documents whose type isn't supported or extraction failed.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id")
    )

    file_path: Mapped[str] = mapped_column(String(1000))
    file_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )


class SyncRun(Base):
    """One record per Moodle sync attempt (backend/moodle/sync_service.py).
    Lets the API report real sync state — in progress, last success time,
    last error — instead of guessing from unrelated timestamps."""
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20))  # "running" | "success" | "error"
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    courses_synced: Mapped[int] = mapped_column(default=0)
    assignments_synced: Mapped[int] = mapped_column(default=0)
    resources_synced: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # TEMPORARY diagnostic fields (see BUILD_LOG.md) — a JSON-serialized list of
    # moodle/sync_service.py's sanitized login-diagnostic snapshots, and when the
    # most recent one was captured. Database-backed (not an in-process variable)
    # specifically because in-memory state was found not to be reliably visible
    # from a separate API request on Render — see BUILD_LOG.md's diagnosis.
    login_diagnostics: Mapped[str | None] = mapped_column(Text, nullable=True)
    login_diagnostics_captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
