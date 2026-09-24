import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from . import queries
from .auth import get_current_user_id
from .chat_agent import handle_chat_message
from .deps import get_db
from .notifications import build_proactive_notifications, compute_new_items_count
from .schemas import (
    AssignmentOut,
    ChatRequestIn,
    ChatResponseOut,
    CourseOut,
    DashboardSummaryOut,
    DocumentOut,
    MoodleSessionStatusOut,
    ResourceOut,
    SpeechRequestIn,
    SyncStatusOut,
    SyncTriggerOut,
    TranscriptionOut,
    UserOut,
)
from storage.crud import finish_sync_run, try_start_new_sync_run
from storage.paths import resolve_document_path

logger = logging.getLogger("esmerelda.sync")

router = APIRouter(prefix="/api")


def _run_moodle_sync(run_id: int, user_id: int) -> None:
    logger.info("[SYNC DEBUG] Moodle sync task entered (run_id=%s, user=%s)", run_id, user_id)
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

        result = run_sync(run_id=run_id, user_id=user_id)
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
def list_courses(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    return [queries.course_out(c) for c in queries.fetch_courses(db, user_id)]


@router.get("/courses/{course_id}", response_model=CourseOut)
def get_course(course_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    course = queries.fetch_course(db, course_id, user_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return queries.course_out(course)


@router.get("/courses/{course_id}/assignments", response_model=list[AssignmentOut])
def list_course_assignments(
    course_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)
):
    course = queries.fetch_course(db, course_id, user_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return [queries.assignment_out(a, course) for a in queries.fetch_course_assignments(db, course_id, user_id)]


@router.get("/courses/{course_id}/resources", response_model=list[ResourceOut])
def list_course_resources(
    course_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)
):
    course = queries.fetch_course(db, course_id, user_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return [queries.resource_out(r, course) for r in queries.fetch_course_resources(db, course_id, user_id)]


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    return [queries.assignment_out(a, c) for a, c in queries.fetch_all_assignments(db, user_id)]


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    return [
        queries.document_out(doc, course, latest_at)
        for doc, _resource, course, latest_at in queries.fetch_all_documents(db, user_id)
    ]


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)
):
    document = queries.fetch_document(db, document_id, user_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    resolved_path = resolve_document_path(document.file_path)
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="File is no longer available on disk")
    return FileResponse(resolved_path, filename=document.name)


@router.get("/sync-status", response_model=SyncStatusOut)
def get_sync_status(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    return queries.fetch_sync_status(db, user_id)


@router.post("/sync/moodle", response_model=SyncTriggerOut, status_code=202)
def trigger_moodle_sync(
    background_tasks: BackgroundTasks, user_id: int = Depends(get_current_user_id)
):
    # try_start_new_sync_run() checks for an already-running sync and
    # creates the new "running" row as a single atomic database
    # statement — not two separate calls (the previous shape here) — so
    # two near-simultaneous requests can't both see "nothing running"
    # and both go on to launch their own competing Playwright session
    # against the same Moodle account. See storage/crud.py for why that
    # was a real, not just theoretical, race. Scoped to user_id (Part 9)
    # so two different users' syncs never block each other.
    run = try_start_new_sync_run(user_id)
    if run is None:
        raise HTTPException(status_code=409, detail="A Moodle sync is already in progress.")

    background_tasks.add_task(_run_moodle_sync, run.id, user_id)
    return SyncTriggerOut(runId=run.id, state="syncing")


@router.post("/users", response_model=UserOut)
def create_user(email: str, display_name: str | None = None, db: Session = Depends(get_db)):
    """Dev/testing helper (Part 11's multi-user test matrix needs two real,
    distinct users to prove isolation) — creates a new Esmerelda User row.
    See api/auth.py's module docstring: this has no password/verification
    of any kind, consistent with this whole module being an explicit
    development identity mechanism, not production signup."""
    from storage.models import User

    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        return UserOut(
            id=existing.id, email=existing.email, displayName=existing.display_name,
            moodleSessionStatus=existing.moodle_session_status,
        )
    user = User(email=email, display_name=display_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, displayName=user.display_name, moodleSessionStatus=None)


@router.get("/users/me", response_model=UserOut)
def get_current_user(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    from storage.models import User

    user = db.get(User, user_id)
    return UserOut(
        id=user.id, email=user.email, displayName=user.display_name, moodleSessionStatus=user.moodle_session_status
    )


@router.get("/moodle/session-status", response_model=MoodleSessionStatusOut)
def get_moodle_session_status(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """Part 3/7: reports whether THIS user's Moodle session is still
    valid, WITHOUT logging in — see moodle/browser.py's
    check_moodle_session(). A user who has never connected at all
    (User.moodle_session_status still NULL) skips the live browser check
    entirely — there's no session on disk worth checking yet."""
    from datetime import datetime

    from moodle.browser import check_moodle_session
    from storage.models import User

    user = db.get(User, user_id)
    if user.moodle_session_status is None:
        return MoodleSessionStatusOut(status="never_connected", checkedAt=None)

    status = check_moodle_session(user_id)
    user.moodle_session_status = status
    user.moodle_session_checked_at = datetime.utcnow()
    db.commit()
    return MoodleSessionStatusOut(status=status, checkedAt=user.moodle_session_checked_at)


@router.post("/moodle/connect", response_model=MoodleSessionStatusOut)
def connect_moodle(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """Part 7's manual connect flow. See moodle/browser.py's
    connect_user_interactively() docstring for this endpoint's real,
    stated limitation: it opens a REAL browser window on the machine
    running this backend process, not the end user's own device — viable
    for local/dev use (what this batch's own tests exercise), not for a
    real hosted multi-tenant deployment. Blocks the HTTP request for up
    to 3 minutes (the manual sign-in itself) — acceptable for this
    explicitly local/dev flow, not something a production version would
    do this way."""
    from datetime import datetime

    from moodle.browser import connect_user_interactively
    from storage.models import User

    result = connect_user_interactively(user_id)
    status = "connected" if result == "connected" else "expired"

    user = db.get(User, user_id)
    user.moodle_session_status = status
    user.moodle_session_checked_at = datetime.utcnow()
    db.commit()
    return MoodleSessionStatusOut(status=status, checkedAt=user.moodle_session_checked_at)


@router.post("/moodle/disconnect", response_model=MoodleSessionStatusOut)
def disconnect_moodle(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """Explicit disconnect (Part 3's "user explicitly disconnects" case) —
    deletes this user's persisted Playwright profile directory entirely
    (their session cookies, specifically — never touches any other
    user's), so the next connect/sync starts from a genuinely clean
    slate rather than a stale or ambiguous state."""
    import shutil
    from datetime import datetime

    from storage.models import User
    from storage.paths import get_user_browser_profile_dir

    profile_dir = get_user_browser_profile_dir(user_id)
    shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    user = db.get(User, user_id)
    user.moodle_session_status = None
    user.moodle_session_checked_at = datetime.utcnow()
    db.commit()
    return MoodleSessionStatusOut(status="never_connected", checkedAt=user.moodle_session_checked_at)


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
def get_dashboard_summary(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    courses_count, assignments_count, resources_count, documents_count = queries.fetch_dashboard_counts(db, user_id)
    return DashboardSummaryOut(
        coursesCount=courses_count,
        assignmentsCount=assignments_count,
        resourcesCount=resources_count,
        documentsCount=documents_count,
        sync=queries.fetch_sync_status(db, user_id),
        newItemsCount=compute_new_items_count(user_id),
        notifications=build_proactive_notifications(db, user_id),
    )


@router.post("/chat", response_model=ChatResponseOut)
async def chat(
    payload: ChatRequestIn, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)
):
    history = [{"role": turn.role, "content": turn.content} for turn in payload.history]
    return await handle_chat_message(
        db, payload.message, user_id, history=history, conversation_id=payload.conversationId
    )


@router.post("/speech")
def synthesize_speech(payload: SpeechRequestIn):
    from .tts import TtsUnavailableError, synthesize_speech as tts_synthesize

    try:
        audio = tts_synthesize(payload.text)
    except TtsUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(content=audio, media_type="audio/wav")


@router.post("/transcribe", response_model=TranscriptionOut)
async def transcribe(audio: UploadFile = File(...)):
    from .stt import SttUnavailableError, transcribe_speech

    audio_bytes = await audio.read()
    try:
        text = await transcribe_speech(audio_bytes, audio.filename or "recording.webm", audio.content_type)
    except SttUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return TranscriptionOut(text=text)
