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
