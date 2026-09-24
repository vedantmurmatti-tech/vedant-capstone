"""Shared data-access layer for the Esmerelda API.

Every DB query and every ORM-row-to-response-model conversion lives here so
`routes.py` (the HTTP layer) and `chat_agent.py` (the chat agent's "act"
phase) call the exact same functions instead of duplicating query logic.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from storage.models import Assignment, Course, Document, DocumentVersion, Resource, SyncRun

from .presenters import derive_file_type, derive_short_name
from .schemas import AssignmentOut, CourseOut, DocumentOut, ResourceOut, SyncStatusOut

DocumentRow = tuple[Document, Resource | None, Course | None, datetime | None]


# --- ORM -> response model -----------------------------------------------


def course_out(course: Course) -> CourseOut:
    return CourseOut(
        id=course.id,
        moodleId=course.moodle_id,
        name=course.name,
        shortName=course.short_name or derive_short_name(course.name),
        description=course.description,
    )


def assignment_out(assignment: Assignment, course: Course) -> AssignmentOut:
    return AssignmentOut(
        id=assignment.id,
        moodleId=assignment.moodle_id,
        courseId=assignment.course_id,
        courseName=course.name,
        courseShortName=course.short_name or derive_short_name(course.name),
        name=assignment.name,
        description=assignment.description,
        dueDate=assignment.due_date,
        submissionUrl=assignment.submission_url,
        submissionStatus=assignment.submission_status,
    )


def resource_out(resource: Resource, course: Course) -> ResourceOut:
    return ResourceOut(
        id=resource.id,
        moodleId=resource.moodle_id,
        courseId=resource.course_id,
        courseName=course.name,
        name=resource.name,
        resourceType=(resource.resource_type or "other").lower(),
        url=resource.url,
        description=resource.description,
    )


def document_out(document: Document, course: Course | None, updated_at: datetime | None) -> DocumentOut:
    # Previously always "indexed", regardless of whether any real
    # searchable text had ever actually been extracted — now reflects
    # storage/text_extraction.py's real result (see
    # storage/models.py's Document.extracted_text / BUILD_LOG.md's
    # Knowledge Base retrieval entry): "indexed" only when this document
    # genuinely has extracted, chat-searchable text. "queued" (not
    # "not indexed" — frontend/src/types/index.ts's IndexingStatus type
    # and DocumentIndexRow.tsx's `indexingMeta` lookup only recognize
    # "indexed"/"indexing"/"queued"; a value outside that set would make
    # `indexing.tone` undefined and crash that row's render — checked
    # directly before choosing this value) for a real download whose type
    # isn't extractable (e.g. a ZIP) or whose extraction failed.
    return DocumentOut(
        id=document.id,
        resourceId=document.resource_id,
        courseId=course.id if course else None,
        courseName=course.name if course else None,
        name=document.name,
        fileType=derive_file_type(document.name),
        updatedAt=updated_at,
        indexingStatus="indexed" if document.extracted_text else "queued",
        downloadUrl=f"/api/documents/{document.id}/download",
    )


# --- queries ---------------------------------------------------------------


def fetch_courses(db: Session) -> list[Course]:
    return db.query(Course).order_by(Course.name).all()


def fetch_course(db: Session, course_id: int) -> Course | None:
    return db.get(Course, course_id)


def fetch_course_assignments(db: Session, course_id: int) -> list[Assignment]:
    return (
        db.query(Assignment)
        .filter(Assignment.course_id == course_id)
        .order_by(Assignment.due_date.is_(None), Assignment.due_date)
        .all()
    )


def fetch_course_resources(db: Session, course_id: int) -> list[Resource]:
    return db.query(Resource).filter(Resource.course_id == course_id).order_by(Resource.name).all()


def fetch_resource_by_moodle_id(db: Session, moodle_id: str) -> Resource | None:
    """content_radar.py gives an assignment's own Moodle activity link a
    Resource row sharing the same moodle_id as the Assignment — this is
    the real, reliable join between the two tables, used by the
    assignment-action-planner Skill to find an assignment's own resource
    entry (and, from there, any document downloaded for it)."""
    return db.scalar(select(Resource).where(Resource.moodle_id == moodle_id))


def fetch_documents_for_resource(db: Session, resource_id: int) -> list[Document]:
    return db.query(Document).filter(Document.resource_id == resource_id).all()


def fetch_all_assignments(db: Session) -> list[tuple[Assignment, Course]]:
    return (
        db.query(Assignment, Course)
        .join(Course, Assignment.course_id == Course.id)
        .order_by(Assignment.due_date.is_(None), Assignment.due_date)
        .all()
    )


def fetch_all_documents(db: Session) -> list[DocumentRow]:
    latest_version = (
        db.query(
            DocumentVersion.document_id.label("document_id"),
            func.max(DocumentVersion.created_at).label("latest_at"),
        )
        .group_by(DocumentVersion.document_id)
        .subquery()
    )

    return (
        db.query(Document, Resource, Course, latest_version.c.latest_at)
        .outerjoin(Resource, Document.resource_id == Resource.id)
        .outerjoin(Course, Resource.course_id == Course.id)
        .outerjoin(latest_version, latest_version.c.document_id == Document.id)
        .order_by(latest_version.c.latest_at.is_(None), latest_version.c.latest_at.desc())
        .all()
    )


def fetch_document(db: Session, document_id: int) -> Document | None:
    return db.get(Document, document_id)


def fetch_sync_status(db: Session) -> SyncStatusOut:
    courses_count = db.query(func.count(Course.id)).scalar() or 0
    try:
        latest_run = db.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
    except ValueError:
        # A malformed (non-null but unparseable) started_at/finished_at on
        # the latest row makes SQLAlchemy's own typed DateTime column
        # raise a bare ValueError while hydrating the ORM object —
        # confirmed directly, not assumed (see storage/crud.py's
        # get_running_sync_run()/_parse_started_at() for the same
        # underlying issue and a fuller explanation). Rather than let
        # that crash this whole endpoint (and therefore the dashboard),
        # report it as an honest error state instead.
        return SyncStatusOut(
            state="error",
            lastSyncedAt=None,
            coursesTracked=courses_count,
            lastError="The most recent sync run has malformed timestamp data and could not be read.",
        )

    if latest_run is None:
        # No sync has ever run through the Moodle sync pipeline (moodle/sync_service.py)
        # on this database — fall back to the pre-sync-pipeline heuristic (a document
        # having been downloaded at some point) so an existing, already-populated local
        # database doesn't regress to "stale" the moment this code ships.
        last_synced_at = db.query(func.max(DocumentVersion.created_at)).scalar()
        return SyncStatusOut(
            state="synced" if last_synced_at else "stale",
            lastSyncedAt=last_synced_at,
            coursesTracked=courses_count,
            lastError=None,
        )

    stale_error_message: str | None = None
    if latest_run.status == "running":
        # storage.crud.get_running_sync_run() is the one place that actually
        # rewrites an abandoned "running" row to "error" (see its docstring
        # for why: the process that owned it may have crashed before ever
        # reporting a result) — but that only happens when someone next
        # tries to trigger a sync. A dashboard that's just polling status,
        # with nobody re-triggering a sync, would otherwise show "syncing"
        # forever for a run that's actually long dead. This is a read-only
        # check (a GET endpoint shouldn't have the side effect of mutating
        # the database on every poll) that reports the same honest state
        # without writing anything — the real DB row is only rewritten the
        # next time get_running_sync_run() is actually called.
        from storage.crud import _get_stale_sync_run_minutes

        threshold_minutes = _get_stale_sync_run_minutes()
        if latest_run.started_at is None:
            state = "error"
            stale_error_message = (
                "Sync run appears abandoned: its started_at timestamp is missing/invalid, so its real "
                "age can't be determined."
            )
        else:
            age = datetime.utcnow() - latest_run.started_at
            if age > timedelta(minutes=threshold_minutes):
                state = "error"
                stale_error_message = (
                    f"Sync run appears abandoned: still marked 'running' after {age}, longer than the "
                    f"{threshold_minutes}-minute staleness threshold. The process that started it "
                    "likely crashed before reporting a result."
                )
            else:
                state = "syncing"
    elif latest_run.status == "error":
        state = "error"
    else:
        state = "synced"

    last_success = db.scalar(
        select(SyncRun).where(SyncRun.status == "success").order_by(SyncRun.id.desc())
    )
    return SyncStatusOut(
        state=state,
        lastSyncedAt=last_success.finished_at if last_success else None,
        coursesTracked=courses_count,
        lastError=stale_error_message or (latest_run.error_message if latest_run.status == "error" else None),
    )


def fetch_dashboard_counts(db: Session) -> tuple[int, int, int, int]:
    courses_count = db.query(func.count(Course.id)).scalar() or 0
    assignments_count = db.query(func.count(Assignment.id)).scalar() or 0
    resources_count = db.query(func.count(Resource.id)).scalar() or 0
    documents_count = db.query(func.count(Document.id)).scalar() or 0
    return courses_count, assignments_count, resources_count, documents_count
