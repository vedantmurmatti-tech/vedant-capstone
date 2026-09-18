import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from . import queries
from .chat_agent import handle_chat_message
from .deps import get_db
from .schemas import (
    AssignmentOut,
    ChatRequestIn,
    ChatResponseOut,
    CourseOut,
    DashboardSummaryOut,
    DocumentOut,
    ResourceOut,
    SyncStatusOut,
    SyncTriggerOut,
)
from storage.crud import create_sync_run, finish_sync_run, get_running_sync_run
from storage.paths import resolve_document_path

logger = logging.getLogger("esmerelda.sync")

router = APIRouter(prefix="/api")


def _run_moodle_sync(run_id: int) -> None:
    # Imported lazily so a machine that never triggers a sync (e.g. this
    # process running with Playwright uninstalled, per the Dockerfile's
    # deliberate exclusion — see BUILD_LOG.md) never pays the import cost
    # or risks an ImportError anywhere except inside this one background task.
    from moodle.sync_service import MoodleCredentialsError, MoodleLoginError, MoodleSyncError, run_sync

    try:
        result = run_sync()
        finish_sync_run(
            run_id,
            status="success",
            courses_synced=result.courses_synced,
            assignments_synced=result.assignments_synced,
            resources_synced=result.resources_synced,
        )
    except (MoodleCredentialsError, MoodleLoginError, MoodleSyncError) as exc:
        logger.warning("Moodle sync run %s failed: %s", run_id, exc)
        finish_sync_run(run_id, status="error", error_message=str(exc))
    except Exception as exc:  # noqa: BLE001 - last-resort guard so a background task never crashes silently
        logger.exception("Moodle sync run %s failed unexpectedly", run_id)
        finish_sync_run(run_id, status="error", error_message=f"Unexpected error: {exc}")


@router.get("/courses", response_model=list[CourseOut])
def list_courses(db: Session = Depends(get_db)):
    return [queries.course_out(c) for c in queries.fetch_courses(db)]


@router.get("/courses/{course_id}", response_model=CourseOut)
def get_course(course_id: int, db: Session = Depends(get_db)):
    course = queries.fetch_course(db, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return queries.course_out(course)


@router.get("/courses/{course_id}/assignments", response_model=list[AssignmentOut])
def list_course_assignments(course_id: int, db: Session = Depends(get_db)):
    course = queries.fetch_course(db, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return [queries.assignment_out(a, course) for a in queries.fetch_course_assignments(db, course_id)]


@router.get("/courses/{course_id}/resources", response_model=list[ResourceOut])
def list_course_resources(course_id: int, db: Session = Depends(get_db)):
    course = queries.fetch_course(db, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return [queries.resource_out(r, course) for r in queries.fetch_course_resources(db, course_id)]


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(db: Session = Depends(get_db)):
    return [queries.assignment_out(a, c) for a, c in queries.fetch_all_assignments(db)]


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    return [
        queries.document_out(doc, course, latest_at)
        for doc, _resource, course, latest_at in queries.fetch_all_documents(db)
    ]


@router.get("/documents/{document_id}/download")
def download_document(document_id: int, db: Session = Depends(get_db)):
    document = queries.fetch_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    resolved_path = resolve_document_path(document.file_path)
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File is no longer available on disk")
    return FileResponse(resolved_path, filename=document.name)


@router.get("/sync-status", response_model=SyncStatusOut)
def get_sync_status(db: Session = Depends(get_db)):
    return queries.fetch_sync_status(db)


@router.post("/sync/moodle", response_model=SyncTriggerOut, status_code=202)
def trigger_moodle_sync(background_tasks: BackgroundTasks):
    if get_running_sync_run() is not None:
        raise HTTPException(status_code=409, detail="A Moodle sync is already in progress.")

    run = create_sync_run()
    background_tasks.add_task(_run_moodle_sync, run.id)
    return SyncTriggerOut(runId=run.id, state="syncing")


@router.get("/dashboard/summary", response_model=DashboardSummaryOut)
def get_dashboard_summary(db: Session = Depends(get_db)):
    courses_count, assignments_count, documents_count = queries.fetch_dashboard_counts(db)
    return DashboardSummaryOut(
        coursesCount=courses_count,
        assignmentsCount=assignments_count,
        documentsCount=documents_count,
        sync=queries.fetch_sync_status(db),
    )


@router.post("/chat", response_model=ChatResponseOut)
async def chat(payload: ChatRequestIn, db: Session = Depends(get_db)):
    return await handle_chat_message(db, payload.message)
