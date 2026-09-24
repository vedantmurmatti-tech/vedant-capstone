from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, DateTime
from datetime import datetime

from .database import Base


class User(Base):
    """An Esmerelda user (a FLAME student), distinct from a Moodle course —
    the root of the ownership tree every other table below hangs off via
    `user_id`. See BUILD_LOG.md's multi-user foundation entry for the full
    architecture, the migration that introduced this table on an
    already-populated single-user database, and this table's current
    dev-only identification mechanism (api/auth.py) — NOT production
    authentication.

    `moodle_session_status`/`moodle_session_checked_at` are a fast,
    queryable summary of this user's Playwright session state; the actual
    session (cookies etc.) lives on disk at
    moodle/browser_profiles/<user.id>/ (see moodle/browser.py), never in
    this database — refreshed by moodle/sync_service.py's
    check_moodle_session()."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    moodle_session_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    moodle_session_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    moodle_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    short_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)

class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    moodle_id: Mapped[str] = mapped_column(String(255))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    submission_url: Mapped[str | None] = mapped_column(String(500))
    submission_status: Mapped[str | None] = mapped_column(String(255))

class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    moodle_id: Mapped[str] = mapped_column(String(255))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))

    name: Mapped[str] = mapped_column(String(255))
    resource_type: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(Text)

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
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
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
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


class AppMeta(Base):
    """Tiny generic key/value table for one-off, idempotent migration
    markers (see storage/database.py's _migrate_due_dates_to_utc() and
    _migrate_assign_existing_data_to_demo_user()) — records that a
    one-time DATA transformation (not just a schema/column addition,
    which _migrate_add_missing_columns() already handles) has already run,
    so it's never silently re-applied to already-converted data on a
    later restart."""
    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))
