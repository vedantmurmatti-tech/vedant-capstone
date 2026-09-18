import json
from datetime import datetime
from .models import Course, Assignment
from sqlalchemy import select
from .models import Resource
from .database import SessionLocal
from .models import Course
from .models import Document, DocumentVersion
from .models import SyncRun

def save_course(
    moodle_id: str,
    name: str,
    short_name: str | None = None,
    description: str | None = None
):
    with SessionLocal() as session:
        course = session.scalar(
            select(Course).where(Course.moodle_id == moodle_id)
        )

        if course is None:
            course = Course(
                moodle_id=moodle_id,
                name=name,
                short_name=short_name,
                description=description
            )
            session.add(course)
        else:
            course.name = name
            course.short_name = short_name
            course.description = description

        session.commit()
        session.refresh(course)
        return course





def save_assignment(
    moodle_id: str,
    course_name: str | None,
    name: str,
    submission_url: str | None = None,
    due_date: datetime | None = None,
    submission_status: str | None = None
):
    with SessionLocal() as session:
        course = None

        if course_name:
            course = session.scalar(
                select(Course).where(Course.name == course_name)
            )

        if course is None:
            print(f"Course not found for assignment: {name}")
            return None

        assignment = session.scalar(
            select(Assignment).where(
                Assignment.moodle_id == moodle_id
            )
        )

        if assignment is None:
            assignment = Assignment(
                moodle_id=moodle_id,
                course_id=course.id,
                name=name,
                submission_url=submission_url,
                due_date=due_date,
                submission_status=submission_status
            )
            session.add(assignment)

        else:
            assignment.name = name
            assignment.course_id = course.id
            assignment.submission_url = submission_url
            assignment.due_date = due_date
            if submission_status is not None:
                assignment.submission_status = submission_status

        session.commit()
        session.refresh(assignment)

        return assignment




def save_resource(
    moodle_id: str,
    course_name: str,
    name: str,
    resource_type: str | None = None,
    url: str | None = None,
    description: str | None = None
):
    with SessionLocal() as session:
        course = session.scalar(
            select(Course).where(Course.name == course_name)
        )

        if course is None:
            print(f"Course not found for resource: {name}")
            return None

        resource = session.scalar(
            select(Resource).where(
                Resource.moodle_id == moodle_id
            )
        )

        if resource is None:
            resource = Resource(
                moodle_id=moodle_id,
                course_id=course.id,
                name=name,
                resource_type=resource_type,
                url=url,
                description=description
            )
            session.add(resource)

        else:
            resource.course_id = course.id
            resource.name = name
            resource.resource_type = resource_type
            resource.url = url
            resource.description = description

        session.commit()
        session.refresh(resource)

        return resource

def save_document(
    name: str,
    file_path: str,
    file_hash: str,
    resource_id: int | None = None
):
    with SessionLocal() as session:
        document = session.query(Document).filter(
            Document.file_path == file_path
        ).first()

        if document is None:
            document = Document(
                name=name,
                file_path=file_path,
                current_hash=file_hash,
                resource_id=resource_id
            )

            session.add(document)
            session.flush()

            version = DocumentVersion(
                document_id=document.id,
                file_path=file_path,
                file_hash=file_hash
            )

            session.add(version)

        elif document.current_hash != file_hash:
            document.current_hash = file_hash

            if resource_id is not None:
                document.resource_id = resource_id

            version = DocumentVersion(
                document_id=document.id,
                file_path=file_path,
                file_hash=file_hash
            )

            session.add(version)

        session.commit()
        session.refresh(document)

        return document


def get_running_sync_run() -> SyncRun | None:
    """Used to reject a new sync trigger while one is already in progress
    (see api/routes.py's POST /api/sync/moodle) — prevents two concurrent
    Playwright sessions from racing over the same Moodle login/profile."""
    with SessionLocal() as session:
        run = session.scalar(
            select(SyncRun).where(SyncRun.status == "running").order_by(SyncRun.id.desc())
        )
        if run is not None:
            session.expunge(run)
        return run


def create_sync_run() -> SyncRun:
    with SessionLocal() as session:
        run = SyncRun(status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        session.expunge(run)
        return run


def finish_sync_run(
    run_id: int,
    *,
    status: str,
    courses_synced: int = 0,
    assignments_synced: int = 0,
    resources_synced: int = 0,
    error_message: str | None = None
) -> None:
    with SessionLocal() as session:
        run = session.get(SyncRun, run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.utcnow()
        run.courses_synced = courses_synced
        run.assignments_synced = assignments_synced
        run.resources_synced = resources_synced
        run.error_message = error_message
        session.commit()


def get_latest_sync_run() -> SyncRun | None:
    with SessionLocal() as session:
        run = session.scalar(
            select(SyncRun).order_by(SyncRun.id.desc())
        )
        if run is not None:
            session.expunge(run)
        return run


# TEMPORARY diagnostic functions (see BUILD_LOG.md) — database-backed
# specifically because an earlier, in-process-only implementation was
# found not to be reliably visible from a separate API request on
# Render (multiple container instances / process restarts don't share
# Python memory, but they do share this one SQLite database).

def record_login_diagnostic(run_id: int, snapshot: dict) -> None:
    """Appends one sanitized diagnostic snapshot (moodle/sync_service.py's
    _capture_login_diagnostics()) to the given SyncRun's running list,
    persisting immediately — not batched until the sync finishes — so a
    GET /api/sync/moodle/diagnostics request can see a snapshot the
    moment it's captured, even mid-sync, even from a different process
    than the one running the sync."""
    with SessionLocal() as session:
        run = session.get(SyncRun, run_id)
        if run is None:
            return
        existing: list = json.loads(run.login_diagnostics) if run.login_diagnostics else []
        existing.append(snapshot)
        run.login_diagnostics = json.dumps(existing)
        run.login_diagnostics_captured_at = datetime.utcnow()
        session.commit()


def get_latest_login_diagnostics() -> dict:
    """The most recent SyncRun that has captured at least one diagnostic
    snapshot (not necessarily the very latest SyncRun overall — a sync
    that failed before reaching _login() at all, e.g. missing
    credentials, has no snapshots, and shouldn't hide an earlier run's
    real captures). Returns a dict with hasCapture=False and no run id
    if no SyncRun has ever captured anything in this database."""
    with SessionLocal() as session:
        run = session.scalar(
            select(SyncRun)
            .where(SyncRun.login_diagnostics.is_not(None))
            .order_by(SyncRun.id.desc())
        )
        if run is None:
            return {"hasCapture": False, "syncRunId": None, "capturedAt": None, "diagnostics": []}
        return {
            "hasCapture": True,
            "syncRunId": run.id,
            "capturedAt": run.login_diagnostics_captured_at,
            "diagnostics": json.loads(run.login_diagnostics) if run.login_diagnostics else [],
        }
