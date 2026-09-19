import json
import logging
import os
from datetime import datetime, timedelta
from .models import Course, Assignment
from sqlalchemy import select, text
from .models import Resource
from .database import SessionLocal
from .models import Course
from .models import Document, DocumentVersion
from .models import SyncRun

logger = logging.getLogger("esmerelda.sync")

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
            # A field is only overwritten when the caller actually has a new
            # value for it. Earlier this unconditionally assigned every
            # field, so a sync pass that didn't happen to scrape a
            # short_name/description (they aren't always available) would
            # silently wipe out a previously-known real value with None.
            course.name = name
            if short_name is not None:
                course.short_name = short_name
            if description is not None:
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
    submission_status: str | None = None,
    course_moodle_id: str | None = None,
    assignment_url: str | None = None,
    description: str | None = None,
):
    """Upserts an assignment by its stable Moodle activity id.

    Course lookup prefers `course_moodle_id` (a stable Moodle course id,
    e.g. scraped from a nearby /course/view.php?id=N link) over
    `course_name` (a fragile, scraped display string that can fail to
    exact-match Course.name for trivial reasons — whitespace, HTML
    entities, a course being renamed). `course_name` is used only as a
    fallback when no stable id is available or the id lookup misses.
    """
    with SessionLocal() as session:
        course = None
        lookup_method = None

        if course_moodle_id:
            course = session.scalar(
                select(Course).where(Course.moodle_id == course_moodle_id)
            )
            if course is not None:
                lookup_method = "moodle_id"

        if course is None and course_name:
            course = session.scalar(
                select(Course).where(Course.name == course_name)
            )
            if course is not None:
                lookup_method = "name"

        if course is None:
            available = [
                (c.moodle_id, c.name)
                for c in session.scalars(select(Course)).all()
            ]
            print(
                f"Course not found for assignment: name={name!r} "
                f"moodle_id={moodle_id!r} course_moodle_id={course_moodle_id!r} "
                f"course_name={course_name!r} assignment_url={assignment_url!r} "
                f"available_courses={available}"
            )
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
                submission_status=submission_status,
                description=description,
            )
            session.add(assignment)

        else:
            # Only overwrite a field when the new scrape actually produced a
            # value for it — a pass that couldn't determine a due date (e.g.
            # a transient DOM read failure) must not erase a previously
            # known real due date.
            assignment.name = name
            assignment.course_id = course.id
            if submission_url is not None:
                assignment.submission_url = submission_url
            if due_date is not None:
                assignment.due_date = due_date
            if submission_status is not None:
                assignment.submission_status = submission_status
            if description is not None:
                assignment.description = description

        session.commit()
        session.refresh(assignment)

        if lookup_method == "name":
            print(
                f"Assignment {name!r} (moodle_id={moodle_id!r}) matched course "
                f"{course.name!r} by name fallback, not by stable moodle_id "
                f"(course_moodle_id={course_moodle_id!r} was not resolvable)."
            )

        return assignment




def save_resource(
    moodle_id: str,
    course_name: str,
    name: str,
    resource_type: str | None = None,
    url: str | None = None,
    description: str | None = None,
    course_moodle_id: str | None = None,
):
    with SessionLocal() as session:
        course = None
        lookup_method = None

        if course_moodle_id:
            course = session.scalar(
                select(Course).where(Course.moodle_id == course_moodle_id)
            )
            if course is not None:
                lookup_method = "moodle_id"

        if course is None and course_name:
            course = session.scalar(
                select(Course).where(Course.name == course_name)
            )
            if course is not None:
                lookup_method = "name"

        if course is None:
            available = [
                (c.moodle_id, c.name)
                for c in session.scalars(select(Course)).all()
            ]
            print(
                f"Course not found for resource: name={name!r} moodle_id={moodle_id!r} "
                f"course_moodle_id={course_moodle_id!r} course_name={course_name!r} "
                f"url={url!r} available_courses={available}"
            )
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
            if resource_type is not None:
                resource.resource_type = resource_type
            if url is not None:
                resource.url = url
            if description is not None:
                resource.description = description

        session.commit()
        session.refresh(resource)

        if lookup_method == "name":
            print(
                f"Resource {name!r} (moodle_id={moodle_id!r}) matched course "
                f"{course.name!r} by name fallback, not by stable moodle_id."
            )

        return resource

def save_document(
    name: str,
    file_path: str,
    file_hash: str,
    resource_id: int | None = None,
    file_type: str | None = None,
    extracted_text: str | None = None,
):
    with SessionLocal() as session:
        document = session.query(Document).filter(
            Document.file_path == file_path
        ).first()

        if document is None:
            document = Document(
                name=name,
                file_path=file_path,
                file_type=file_type,
                current_hash=file_hash,
                resource_id=resource_id,
                extracted_text=extracted_text,
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
            document.extracted_text = extracted_text

            if resource_id is not None:
                document.resource_id = resource_id
            if file_type is not None:
                document.file_type = file_type

            version = DocumentVersion(
                document_id=document.id,
                file_path=file_path,
                file_hash=file_hash
            )

            session.add(version)

        session.commit()
        session.refresh(document)

        return document


# If the process running a sync is killed outright (an out-of-memory
# kill, a container restart mid-sync, an unhandled fatal signal — all
# real possibilities on a memory-constrained Render instance) before
# api/routes.py's _run_moodle_sync() reaches its own finally-equivalent
# finish_sync_run() call, that SyncRun row is permanently left with
# status="running" — no code path ever runs again to change it.
#
# Chosen default (30 minutes): no real historical sync logs from an
# actual Moodle instance were available to inspect from this environment
# (no deployed Render instance, no real Moodle account) — this is a
# reasoned bound derived from this pipeline's own per-step timeouts, not
# a measured value. A normal sync's worst case is roughly: login (~30s)
# + per-course page visits (30s each) + per-assignment submission-status
# fetches (~20s each) + per-document downloads, each individually capped
# at a 300-second total deadline (moodle/document_downloader.py's
# _TOTAL_DOWNLOAD_DEADLINE_SECONDS). For an institution with many active
# courses and many large documents, that last term alone could
# plausibly add up to more than 30 minutes — e.g. 10+ documents each
# taking close to their own individual cap. Because that worst case
# scales with however much real content an actual Moodle account has
# (unknown from this environment), the threshold is made configurable
# via ESMERELDA_STALE_SYNC_MINUTES rather than hardcoded, so it can be
# raised for a real deployment without a code change if a genuinely
# large, legitimate sync is ever seen tripping it.
_DEFAULT_STALE_SYNC_RUN_MINUTES = 30


def _get_stale_sync_run_minutes() -> int:
    """Re-read on every call (not cached at import time) so a changed
    environment variable takes effect without a process restart being
    required for this specific setting — cheap enough (one env lookup,
    no I/O) that there's no reason to cache it."""
    raw = os.environ.get("ESMERELDA_STALE_SYNC_MINUTES")
    if not raw:
        return _DEFAULT_STALE_SYNC_RUN_MINUTES
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError("must be a positive integer")
        return value
    except ValueError:
        logger.warning(
            "ESMERELDA_STALE_SYNC_MINUTES=%r is not a valid positive integer — falling back to the "
            "default of %d minutes",
            raw, _DEFAULT_STALE_SYNC_RUN_MINUTES,
        )
        return _DEFAULT_STALE_SYNC_RUN_MINUTES


def _parse_started_at(raw) -> datetime | None:
    """Defensive parsing for a SyncRun.started_at value read via raw SQL
    (not the ORM's own typed column access — see get_running_sync_run()'s
    docstring for why). Confirmed directly, not assumed: reading a
    malformed non-null started_at (e.g. a garbage string from manually-
    edited or corrupted data — this column has no application-level
    validation beyond "not null") back through the ORM's normal typed
    attribute access raises a bare ValueError from datetime parsing deep
    inside SQLAlchemy, which would otherwise crash this entire function
    (and therefore every caller: POST /api/sync/moodle, GET
    /api/sync-status) instead of being treated as "can't determine this
    row's age, treat it as stale." Returns None for anything that isn't
    already a datetime and doesn't parse as one — never raises."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def get_running_sync_run() -> SyncRun | None:
    """Used to reject a new sync trigger while one is already in progress
    (see api/routes.py's POST /api/sync/moodle) — prevents two concurrent
    Playwright sessions from racing over the same Moodle login/profile.

    A "running" row older than the configured staleness threshold (see
    _get_stale_sync_run_minutes()) is treated as abandoned rather than
    genuinely in progress: it's rewritten in place to status="error" with
    a clear explanation (so GET /api/sync-status reports it honestly
    instead of "syncing" forever), and this function returns None so a
    new sync isn't blocked indefinitely by a process that no longer
    exists to ever finish it. A row with a missing OR malformed
    started_at (should not happen via this module's own
    create_sync_run(), which always sets a real one, but defensively
    handled rather than trusted) is treated the same way — since its
    true age can't be determined, it's healed immediately.

    Deliberately reads started_at via a raw, untyped SQL SELECT first
    (_parse_started_at()), not `select(SyncRun)`'s normal ORM attribute
    access — the ORM's own DateTime column type raises a ValueError while
    hydrating the row if the stored value isn't a valid ISO timestamp,
    which would crash this whole function before it ever got a chance to
    treat that same problem as "stale, heal it." The full ORM object is
    only ever fetched (via session.get(), by primary key) once the raw
    value has already been confirmed parseable."""
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT id, started_at FROM sync_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1")
        ).first()
        if row is None:
            return None
        run_id, started_at_raw = row

        threshold_minutes = _get_stale_sync_run_minutes()
        started_at = _parse_started_at(started_at_raw)
        if started_at is None:
            age_description = f"unknown (started_at={started_at_raw!r} is missing or unparseable)"
            is_stale = True
        else:
            age = datetime.utcnow() - started_at
            age_description = str(age)
            is_stale = age > timedelta(minutes=threshold_minutes)

        if is_stale:
            # When started_at itself was the problem (missing/unparseable,
            # not just old), it's overwritten here too — leaving the
            # original garbage value in place would turn this row into a
            # permanent landmine for any FUTURE ordinary ORM query that
            # ever touches it again (`select(SyncRun)` with no WHERE, an
            # admin/debug listing, etc. — anything using normal typed
            # column access), which would keep hitting the exact same
            # ValueError this function was just built to route around.
            # Confirmed directly (not assumed): the malformed value in
            # this project's own test for this scenario did exactly that
            # to the test's own follow-up verification query, before this
            # line was added.
            set_started_at = ", started_at = :new_started_at" if started_at is None else ""
            params = {
                "now": datetime.utcnow(),
                "id": run_id,
                "msg": (
                    f"Sync run abandoned: age is {age_description}, "
                    f"{'exceeding' if started_at is not None else 'compared against'} the "
                    f"{threshold_minutes}-minute staleness threshold. The process that started it "
                    "likely crashed or was killed before it could report its own result (e.g. an "
                    "out-of-memory kill or a container restart), or its started_at timestamp was "
                    "missing/invalid. Automatically marked as failed so it doesn't block future syncs."
                ),
            }
            if started_at is None:
                params["new_started_at"] = datetime.utcnow()
            session.execute(
                text(
                    "UPDATE sync_runs SET status = 'error', finished_at = :now, error_message = :msg"
                    f"{set_started_at} WHERE id = :id"
                ),
                params,
            )
            session.commit()
            return None

        run = session.get(SyncRun, run_id)
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


def try_start_new_sync_run() -> SyncRun | None:
    """Atomically checks for an already-running (non-stale) sync AND
    creates a new "running" row, as a single SQL statement — closes a
    real race that existed in api/routes.py's POST /api/sync/moodle
    handler, which used to call get_running_sync_run() and create_sync_run()
    as two separate function calls (two separate transactions). Two
    near-simultaneous requests could both see "nothing running" from the
    first call before either had made its own INSERT from the second —
    both would then create their own "running" row, and both would go on
    to launch a fully separate Playwright/Chromium session logging into
    the same Moodle account at the same time. SQLite serializes writers
    at the file level, and a single INSERT...SELECT...WHERE NOT EXISTS
    statement is atomic with respect to other connections — there is no
    point between "check" and "act" for a second connection to observe,
    because there is only one statement, not two.

    A "running" row counts as blocking only if it's both present AND not
    stale (started_at not null and within the configured threshold —
    same rule as get_running_sync_run(), kept consistent deliberately) —
    an old, abandoned "running" row can never block a new sync from
    starting, without needing a separate healing step to run first and
    without that separate step needing to itself be race-free.

    Returns the newly created SyncRun, or None if a genuinely active
    sync already exists and nothing was created."""
    threshold_minutes = _get_stale_sync_run_minutes()
    stale_cutoff = datetime.utcnow() - timedelta(minutes=threshold_minutes)
    now = datetime.utcnow()

    with SessionLocal() as session:
        result = session.execute(
            text(
                "INSERT INTO sync_runs (status, started_at, courses_synced, assignments_synced, resources_synced) "
                "SELECT 'running', :now, 0, 0, 0 "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM sync_runs "
                "  WHERE status = 'running' AND started_at IS NOT NULL AND started_at > :stale_cutoff"
                ")"
            ),
            {"now": now, "stale_cutoff": stale_cutoff},
        )
        if result.rowcount == 0:
            session.rollback()
            return None

        # Best-effort housekeeping in the same transaction: any other
        # "running" row still around at this point is necessarily one
        # that didn't block the insert above (either stale or missing a
        # timestamp) — rewritten to "error" so it doesn't linger
        # indefinitely as a confusing "running" row in sync history, now
        # that a new run has taken over. Not required for correctness
        # (the new row above is already the one fetch_sync_status() and
        # future calls to this function will see), purely for tidiness.
        session.execute(
            text(
                "UPDATE sync_runs SET status = 'error', finished_at = :now, "
                "error_message = 'Sync run abandoned: superseded by a newer sync after exceeding the "
                f"{threshold_minutes}-minute staleness threshold (or having a missing/invalid started_at).' "
                "WHERE status = 'running' AND id != (SELECT id FROM sync_runs ORDER BY id DESC LIMIT 1)"
            ),
            {"now": now},
        )
        session.commit()

        run = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
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
