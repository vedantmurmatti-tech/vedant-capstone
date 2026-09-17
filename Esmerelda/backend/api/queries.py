"""Shared data-access layer for the Esmerelda API.

Every DB query and every ORM-row-to-response-model conversion lives here so
`routes.py` (the HTTP layer) and `chat_agent.py` (the chat agent's "act"
phase) call the exact same functions instead of duplicating query logic.
"""

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from storage.models import Assignment, Course, Document, DocumentVersion, Resource

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
    return DocumentOut(
        id=document.id,
        resourceId=document.resource_id,
        courseId=course.id if course else None,
        courseName=course.name if course else None,
        name=document.name,
        fileType=derive_file_type(document.name),
        updatedAt=updated_at,
        indexingStatus="indexed",
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
    last_synced_at = db.query(func.max(DocumentVersion.created_at)).scalar()

    return SyncStatusOut(
        state="synced" if last_synced_at else "stale",
        lastSyncedAt=last_synced_at,
        coursesTracked=courses_count,
    )


def fetch_dashboard_counts(db: Session) -> tuple[int, int, int]:
    courses_count = db.query(func.count(Course.id)).scalar() or 0
    assignments_count = db.query(func.count(Assignment.id)).scalar() or 0
    documents_count = db.query(func.count(Document.id)).scalar() or 0
    return courses_count, assignments_count, documents_count
