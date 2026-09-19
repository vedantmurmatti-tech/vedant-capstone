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
from storage.crud import finish_sync_run, try_start_new_sync_run
from storage.paths import resolve_document_path

logger = logging.getLogger("esmerelda.sync")

router = APIRouter(prefix="/api")


def _run_moodle_sync(run_id: int) -> None:
    logger.info("[SYNC DEBUG] Moodle sync task entered (run_id=%s)", run_id)
    try:
        # Imported lazily, and specifically *inside* this try block — not
        # before it. This import itself can fail (e.g. Playwright/its
        # Chromium binary genuinely missing from whatever image is
        # actually running, regardless of what backend/Dockerfile
        # currently installs) with a plain ImportError, which is not one
        # of the MoodleSyncError family below. A previous version of this
        # function had this import ABOVE the try block entirely — an
        # ImportError there would propagate out of this whole
        # BackgroundTasks callback uncaught, meaning finish_sync_run() is
        # never called and this SyncRun row is left at status="running"
        # forever (see storage/crud.py's get_running_sync_run() staleness
        # handling, added as a second, independent safety net for exactly
        # this class of bug — but the real fix is not leaving the row
        # orphaned in the first place).
        from moodle.sync_service import MoodleCredentialsError, MoodleLoginError, MoodleSyncError, run_sync

        result = run_sync(run_id=run_id)
        finish_sync_run(
            run_id,
            status="success",
            courses_synced=result.courses_synced,
            assignments_synced=result.assignments_synced,
            resources_synced=result.resources_synced,
        )
    except ImportError as exc:
        logger.exception("Moodle sync run %s failed: could not import moodle.sync_service", run_id)
        finish_sync_run(
            run_id, status="error",
            error_message=f"Moodle sync is unavailable in this deployment: {exc}",
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
    # try_start_new_sync_run() checks for an already-running sync and
    # creates the new "running" row as a single atomic database
    # statement — not two separate calls (the previous shape here) — so
    # two near-simultaneous requests can't both see "nothing running"
    # and both go on to launch their own competing Playwright session
    # against the same Moodle account. See storage/crud.py for why that
    # was a real, not just theoretical, race.
    run = try_start_new_sync_run()
    if run is None:
        raise HTTPException(status_code=409, detail="A Moodle sync is already in progress.")

    background_tasks.add_task(_run_moodle_sync, run.id)
    return SyncTriggerOut(runId=run.id, state="syncing")


@router.get("/sync/moodle/diagnostics")
def get_moodle_login_diagnostics():
    """TEMPORARY, diagnostic-only endpoint — added specifically to pull
    the real, sanitized post-login page state out of a running Render
    instance without needing shell/log access, after a login failure
    persisted there despite working locally. See BUILD_LOG.md.

    Reads from the database (storage.crud.get_latest_login_diagnostics),
    not an in-process variable — an earlier version read only an
    in-process list, which was found not to be reliably visible from a
    separate API request on Render (a background task and a later GET
    request are not guaranteed to land on the same process/instance).
    The database is shared by every process, so this works regardless of
    how many container instances or worker processes are actually
    running.

    Response shape: `hasCapture` (whether any sync has ever captured a
    diagnostic snapshot in this database), `syncRunId` (which SyncRun it
    belongs to), `capturedAt` (when the most recent snapshot for that run
    was captured), `buildMarker` (moodle/sync_service.py's
    DIAGNOSTIC_BUILD_MARKER — compare this against what the startup log
    printed to confirm the running container is actually this build), and
    `diagnostics` (the stage-by-stage snapshot list itself — URL, title,
    form/usermenu/logout-link presence, any Moodle login-error text, a
    sanitized dump of visible page text, and the forms/inputs present,
    values never included). Every field was already sanitized at capture
    time (moodle/sync_service.py's _sanitize_text()) — the username,
    password, and any email-shaped or opaque-token-shaped text are
    redacted before this is ever written to the database, so this
    endpoint has nothing further to strip.

    Remove this endpoint (and moodle/sync_service.py's matching capture
    code and the SyncRun.login_diagnostics* columns) once the
    investigation it was added for is complete.
    """
    import os

    from moodle.sync_service import DIAGNOSTIC_BUILD_MARKER
    from storage.crud import get_latest_login_diagnostics
    from storage.paths import get_data_dir

    result = get_latest_login_diagnostics()
    result["buildMarker"] = DIAGNOSTIC_BUILD_MARKER
    # No secret here — just the resolved path and whether it's using the
    # container's own (likely non-persistent) filesystem. See main.py's
    # matching startup log line for why this matters: an unset
    # ESMERELDA_DATA_DIR on a platform like Render means every restart or
    # redeploy silently wipes the database and downloaded documents.
    result["dataDir"] = str(get_data_dir())
    result["dataDirEphemeralFallback"] = "ESMERELDA_DATA_DIR" not in os.environ
    return result


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
