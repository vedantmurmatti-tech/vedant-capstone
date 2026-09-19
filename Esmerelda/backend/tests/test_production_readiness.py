"""Tests from a production-readiness audit of the Moodle sync pipeline
(login -> course/assignment/resource discovery -> document download ->
extraction -> persistence -> API/frontend display).

Covers real gaps found and fixed during that audit:
  - A SyncRun row left stuck at status="running" forever if the process
    that owned it crashed/was killed before ever reporting a result
    (get_running_sync_run()'s new staleness recovery, storage/crud.py).
  - api/routes.py's _run_moodle_sync() previously imported
    moodle.sync_service OUTSIDE its own try/except — an ImportError there
    (e.g. Playwright genuinely missing from whatever's actually running)
    would escape the whole BackgroundTasks callback uncaught, leaving the
    SyncRun row orphaned at "running" with no error ever recorded. Now
    inside the try block, with its own explicit except ImportError.
  - Assignment.submission_status (scraped and persisted by the sync
    pipeline) was never exposed through the API's AssignmentOut/
    assignment_out() at all — real data the pipeline already computes was
    silently unavailable to the frontend.

Does not attempt to test real Moodle or Render behavior — only this
project's own database/API code, against a local SQLite database.

Run with:
    venv/Scripts/python.exe tests/test_production_readiness.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        print(f"[PASS] {label}")
        passed += 1
    else:
        print(f"[FAIL] {label}")
        failed += 1


backend_dir = Path(__file__).resolve().parent.parent


def _run(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tempfile.mkdtemp(prefix="esmerelda_prod_readiness_test_")
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# === 1 & 2: stale "running" SyncRun recovery ===
_STALE_RUN_SCRIPT = """
from datetime import datetime, timedelta
from storage.database import init_db, SessionLocal
from storage.models import SyncRun
from storage.crud import get_running_sync_run
init_db()

with SessionLocal() as session:
    stale = SyncRun(status="running", started_at=datetime.utcnow() - timedelta(hours=2))
    session.add(stale)
    session.commit()
    stale_id = stale.id

result = get_running_sync_run()
print(f"RESULT_AFTER_STALE:{result}")

with SessionLocal() as session:
    row = session.get(SyncRun, stale_id)
    print(f"ROW_STATUS:{row.status}")
    print(f"ROW_HAS_ERROR_MESSAGE:{bool(row.error_message)}")
"""

_FRESH_RUN_SCRIPT = """
from storage.database import init_db, SessionLocal
from storage.models import SyncRun
from storage.crud import get_running_sync_run, create_sync_run
init_db()

run = create_sync_run()
result = get_running_sync_run()
print(f"RESULT_AFTER_FRESH:{result is not None}")
print(f"RESULT_ID:{result.id if result else None}:{run.id}")
"""

try:
    proc1 = _run(_STALE_RUN_SCRIPT)
    check("1. Stale-run subprocess exited successfully", proc1.returncode == 0)
    if proc1.returncode != 0:
        print(proc1.stdout)
        print(proc1.stderr)
    check(
        "2. A 'running' SyncRun started 2 hours ago is treated as abandoned — get_running_sync_run() "
        "returns None so a new sync isn't blocked forever by a process that no longer exists",
        "RESULT_AFTER_STALE:None" in proc1.stdout,
    )
    check(
        "2b. The abandoned row itself is rewritten to status='error' with a real explanation, rather than "
        "silently left at 'running' — so GET /api/sync-status reports it honestly",
        "ROW_STATUS:error" in proc1.stdout and "ROW_HAS_ERROR_MESSAGE:True" in proc1.stdout,
    )

    proc2 = _run(_FRESH_RUN_SCRIPT)
    check("3. Fresh-run subprocess exited successfully", proc2.returncode == 0)
    check(
        "4. A genuinely fresh 'running' SyncRun (started moments ago) is NOT treated as stale — still "
        "correctly blocks a concurrent sync trigger (regression guard for the normal, working case)",
        "RESULT_AFTER_FRESH:True" in proc2.stdout,
    )
except Exception as exc:
    check(f"0. Stale-run test setup failed unexpectedly: {exc}", False)


# === 5: read-only staleness reporting in fetch_sync_status() doesn't mutate the DB ===
_STATUS_READONLY_SCRIPT = """
from datetime import datetime, timedelta
from storage.database import init_db, SessionLocal
from storage.models import SyncRun
from api.queries import fetch_sync_status
init_db()

with SessionLocal() as session:
    stale = SyncRun(status="running", started_at=datetime.utcnow() - timedelta(hours=2))
    session.add(stale)
    session.commit()
    stale_id = stale.id

with SessionLocal() as session:
    status = fetch_sync_status(session)
    print(f"STATE:{status.state}")
    print(f"HAS_ERROR:{bool(status.lastError)}")

with SessionLocal() as session:
    row = session.get(SyncRun, stale_id)
    print(f"ROW_STATUS_AFTER_GET:{row.status}")
"""

try:
    proc3 = _run(_STATUS_READONLY_SCRIPT)
    check("5. Read-only status-check subprocess exited successfully", proc3.returncode == 0)
    if proc3.returncode != 0:
        print(proc3.stdout)
        print(proc3.stderr)
    check(
        "6. GET /api/sync-status reports a stale 'running' row as state='error' with a real explanation, "
        "even without anyone re-triggering a sync",
        "STATE:error" in proc3.stdout and "HAS_ERROR:True" in proc3.stdout,
    )
    check(
        "7. That status check is read-only — the underlying SyncRun row is NOT rewritten just from being "
        "viewed (only storage.crud.get_running_sync_run(), called when a new sync is actually triggered, "
        "performs the real rewrite) — a GET endpoint should not have this side effect",
        "ROW_STATUS_AFTER_GET:running" in proc3.stdout,
    )
except Exception as exc:
    check(f"0. Read-only status-check test setup failed unexpectedly: {exc}", False)


# === 8: _run_moodle_sync()'s ImportError safety net ===
_IMPORT_ERROR_SCRIPT = """
import sys
# Forces `import moodle.sync_service` (and `from moodle.sync_service import ...`)
# to raise ImportError, simulating Playwright/Chromium genuinely missing from
# whatever's actually running, WITHOUT needing to actually uninstall anything.
sys.modules["moodle.sync_service"] = None

from storage.database import init_db, SessionLocal
from storage.models import SyncRun
from storage.crud import create_sync_run
from api.routes import _run_moodle_sync
init_db()

run = create_sync_run()
_run_moodle_sync(run.id)

with SessionLocal() as session:
    row = session.get(SyncRun, run.id)
    print(f"FINAL_STATUS:{row.status}")
    print(f"HAS_ERROR_MESSAGE:{bool(row.error_message)}")
"""

try:
    proc4 = _run(_IMPORT_ERROR_SCRIPT)
    check("8. ImportError-simulation subprocess exited successfully (the import failure itself didn't crash the process)", proc4.returncode == 0)
    if proc4.returncode != 0:
        print(proc4.stdout)
        print(proc4.stderr)
    check(
        "9. CRITICAL: when moodle.sync_service genuinely can't be imported, _run_moodle_sync() still calls "
        "finish_sync_run() — the SyncRun ends at status='error' with a real message, NOT stuck at 'running' "
        "forever (the real bug: the import used to happen outside the try/except)",
        "FINAL_STATUS:error" in proc4.stdout and "HAS_ERROR_MESSAGE:True" in proc4.stdout,
    )
except Exception as exc:
    check(f"0. ImportError-simulation test setup failed unexpectedly: {exc}", False)


# === 10: Assignment.submission_status is exposed through the API layer ===
_SUBMISSION_STATUS_SCRIPT = """
from storage.database import init_db, SessionLocal
from storage.models import Course, Assignment
from api.queries import assignment_out
init_db()

with SessionLocal() as session:
    course = Course(moodle_id="1", name="Test Course")
    session.add(course)
    session.flush()
    assignment = Assignment(
        moodle_id="a1", course_id=course.id, name="Test Assignment",
        submission_status="Submitted for grading",
    )
    session.add(assignment)
    session.commit()
    out = assignment_out(assignment, course)
    print(f"SUBMISSION_STATUS:{out.submissionStatus!r}")
"""

try:
    proc5 = _run(_SUBMISSION_STATUS_SCRIPT)
    check("10. submission-status-exposure subprocess exited successfully", proc5.returncode == 0)
    if proc5.returncode != 0:
        print(proc5.stdout)
        print(proc5.stderr)
    check(
        "11. Assignment.submission_status (real data the sync pipeline already scrapes and persists) is "
        "now included in the API's AssignmentOut — previously silently dropped before reaching the frontend",
        "SUBMISSION_STATUS:'Submitted for grading'" in proc5.stdout,
    )
except Exception as exc:
    check(f"0. submission-status-exposure test setup failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
