"""Tests for storage/crud.py's sync-run staleness handling: the
configurable ESMERELDA_STALE_SYNC_MINUTES threshold, and
try_start_new_sync_run() — the atomic check-and-create that replaced a
real check-then-act race in api/routes.py's POST /api/sync/moodle.

No real historical Moodle sync logs were available to inspect from this
environment (no deployed Render instance, no real Moodle account) — the
30-minute default is a reasoned bound derived from this pipeline's own
per-step timeouts (see storage/crud.py's docstring for the full
reasoning), not a measured value. Making the threshold configurable via
an environment variable is what lets a real deployment correct it
without a code change if a genuinely large, legitimate sync is ever
observed tripping it.

Run with:
    venv/Scripts/python.exe tests/test_sync_staleness.py
"""

import os
import subprocess
import sys
import tempfile
import threading
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


def _run(script: str, env_extra: dict | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tempfile.mkdtemp(prefix="esmerelda_sync_staleness_test_")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# === 1: an active sync well below the threshold is not touched ===
_ACTIVE_SCRIPT = """
from datetime import datetime, timedelta
from storage.database import init_db, SessionLocal
from storage.models import SyncRun
from storage.crud import get_running_sync_run
init_db()
with SessionLocal() as session:
    run = SyncRun(status="running", started_at=datetime.utcnow() - timedelta(minutes=5))
    session.add(run)
    session.commit()
    run_id = run.id

result = get_running_sync_run()
print(f"RESULT:{result.id if result else None}")
with SessionLocal() as session:
    row = session.get(SyncRun, run_id)
    print(f"STATUS:{row.status}")
"""

# === 2: a sync above the threshold is healed ===
_STALE_SCRIPT = """
from datetime import datetime, timedelta
from storage.database import init_db, SessionLocal
from storage.models import SyncRun
from storage.crud import get_running_sync_run
init_db()
with SessionLocal() as session:
    run = SyncRun(status="running", started_at=datetime.utcnow() - timedelta(minutes=45))
    session.add(run)
    session.commit()
    run_id = run.id

result = get_running_sync_run()
print(f"RESULT:{result}")
with SessionLocal() as session:
    row = session.get(SyncRun, run_id)
    print(f"STATUS:{row.status}")
    print(f"HAS_ERROR:{bool(row.error_message)}")
"""

# === 3: a NULL started_at is rejected by the schema's own NOT NULL constraint —
# === confirmed directly (not assumed) so this isn't a gap that needs separate handling.
_NULL_TIMESTAMP_REJECTED_SCRIPT = """
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from storage.database import init_db, SessionLocal
init_db()
with SessionLocal() as session:
    try:
        session.execute(text("INSERT INTO sync_runs (status, started_at, courses_synced, assignments_synced, resources_synced) VALUES ('running', NULL, 0, 0, 0)"))
        session.commit()
        print("INSERT_SUCCEEDED")
    except IntegrityError:
        print("INSERT_REJECTED_BY_NOT_NULL_CONSTRAINT")
"""

# === 3b: a malformed (non-null, unparseable) started_at IS reachable in practice
# === (SQLite has no type-affinity enforcement on the actual stored bytes, only the
# === NOT NULL constraint above) — this is the real "invalid timestamp" scenario,
# === and must be treated as stale, not crash the whole staleness check.
_MALFORMED_TIMESTAMP_SCRIPT = """
from sqlalchemy import text
from storage.database import init_db, SessionLocal
from storage.models import SyncRun
from storage.crud import get_running_sync_run
init_db()
with SessionLocal() as session:
    # Bypasses the ORM entirely — simulates corrupted/manually-edited data.
    # SQLite's NOT NULL constraint (confirmed above) blocks a NULL value,
    # but does not validate that a non-null TEXT value is actually a real
    # timestamp — this string is accepted at the database level.
    session.execute(text("INSERT INTO sync_runs (status, started_at, courses_synced, assignments_synced, resources_synced) VALUES ('running', 'not-a-real-timestamp', 0, 0, 0)"))
    session.commit()
    run_id = session.execute(text("SELECT id FROM sync_runs ORDER BY id DESC LIMIT 1")).scalar()

result = get_running_sync_run()
print(f"RESULT:{result}")
with SessionLocal() as session:
    row = session.get(SyncRun, run_id)
    print(f"STATUS:{row.status}")
    print(f"HAS_ERROR:{bool(row.error_message)}")
"""

# === 3c: the same malformed timestamp on the LATEST row must not crash
# === GET /api/sync-status either (api/queries.py's fetch_sync_status()).
_MALFORMED_TIMESTAMP_STATUS_SCRIPT = """
from sqlalchemy import text
from storage.database import init_db, SessionLocal
from api.queries import fetch_sync_status
init_db()
with SessionLocal() as session:
    session.execute(text("INSERT INTO sync_runs (status, started_at, courses_synced, assignments_synced, resources_synced) VALUES ('success', 'garbage-timestamp', 0, 0, 0)"))
    session.commit()

with SessionLocal() as session:
    status = fetch_sync_status(session, None)
    print(f"STATE:{status.state}")
    print(f"HAS_ERROR:{bool(status.lastError)}")
"""

# === 4: the threshold is configurable via ESMERELDA_STALE_SYNC_MINUTES ===
_CONFIGURABLE_THRESHOLD_SCRIPT = """
from datetime import datetime, timedelta
from storage.database import init_db, SessionLocal
from storage.models import SyncRun
from storage.crud import get_running_sync_run
init_db()
with SessionLocal() as session:
    # 10 minutes old — would be "active" under the 30-minute default,
    # but "stale" under a 5-minute configured threshold.
    run = SyncRun(status="running", started_at=datetime.utcnow() - timedelta(minutes=10))
    session.add(run)
    session.commit()
    run_id = run.id

result = get_running_sync_run()
print(f"RESULT:{result}")
with SessionLocal() as session:
    row = session.get(SyncRun, run_id)
    print(f"STATUS:{row.status}")
"""

# === 5: an invalid env var value falls back to the safe default rather than crashing ===
_INVALID_ENV_SCRIPT = """
from storage.crud import _get_stale_sync_run_minutes
print(f"THRESHOLD:{_get_stale_sync_run_minutes()}")
"""

try:
    proc1 = _run(_ACTIVE_SCRIPT)
    check("1. Active-sync subprocess exited successfully", proc1.returncode == 0)
    check(
        "2. A sync started 5 minutes ago (well below the 30-minute default threshold) is correctly "
        "reported as still running, not healed",
        "RESULT:1" in proc1.stdout and "STATUS:running" in proc1.stdout,
    )
except Exception as exc:
    check(f"0. Active-sync test failed unexpectedly: {exc}", False)

try:
    proc2 = _run(_STALE_SCRIPT)
    check("3. Stale-sync subprocess exited successfully", proc2.returncode == 0)
    check(
        "4. A sync started 45 minutes ago (above the 30-minute default threshold) is healed to 'error' "
        "with a real explanation, and no longer blocks new syncs",
        "RESULT:None" in proc2.stdout and "STATUS:error" in proc2.stdout and "HAS_ERROR:True" in proc2.stdout,
    )
except Exception as exc:
    check(f"0. Stale-sync test failed unexpectedly: {exc}", False)

try:
    proc3a = _run(_NULL_TIMESTAMP_REJECTED_SCRIPT)
    check("5a. NULL-timestamp-rejection subprocess exited successfully", proc3a.returncode == 0)
    check(
        "5b. A NULL started_at is rejected by the schema's own NOT NULL constraint before it can ever "
        "reach the staleness check — confirmed directly, not assumed to be a gap needing its own handling",
        "INSERT_REJECTED_BY_NOT_NULL_CONSTRAINT" in proc3a.stdout,
    )
except Exception as exc:
    check(f"0. NULL-timestamp-rejection test failed unexpectedly: {exc}", False)

try:
    proc3 = _run(_MALFORMED_TIMESTAMP_SCRIPT)
    check("6. Malformed-timestamp subprocess exited successfully (no crash on an unparseable started_at)", proc3.returncode == 0)
    if proc3.returncode != 0:
        print(proc3.stdout)
        print(proc3.stderr)
    check(
        "7. CRITICAL: a 'running' row with a malformed (non-null, unparseable) started_at is treated as "
        "stale and healed, rather than raising an unhandled ValueError while trying to hydrate the row "
        "through the ORM's typed DateTime column (confirmed as the real, reproducible failure mode before "
        "this fix — not a hypothetical)",
        "RESULT:None" in proc3.stdout and "STATUS:error" in proc3.stdout and "HAS_ERROR:True" in proc3.stdout,
    )
except Exception as exc:
    check(f"0. Malformed-timestamp test failed unexpectedly: {exc}", False)

try:
    proc3c = _run(_MALFORMED_TIMESTAMP_STATUS_SCRIPT)
    check("8. Malformed-timestamp status-check subprocess exited successfully", proc3c.returncode == 0)
    if proc3c.returncode != 0:
        print(proc3c.stdout)
        print(proc3c.stderr)
    check(
        "9. GET /api/sync-status's underlying fetch_sync_status() also doesn't crash on the same malformed "
        "timestamp, even on a non-'running' row — reports a clear error state instead",
        "STATE:error" in proc3c.stdout and "HAS_ERROR:True" in proc3c.stdout,
    )
except Exception as exc:
    check(f"0. Malformed-timestamp status-check test failed unexpectedly: {exc}", False)

try:
    proc4 = _run(_CONFIGURABLE_THRESHOLD_SCRIPT, env_extra={"ESMERELDA_STALE_SYNC_MINUTES": "5"})
    check("7. Configurable-threshold subprocess exited successfully", proc4.returncode == 0)
    check(
        "8. ESMERELDA_STALE_SYNC_MINUTES=5 makes a 10-minute-old run count as stale, even though it would "
        "be 'active' under the 30-minute default — the threshold genuinely takes effect from the env var",
        "RESULT:None" in proc4.stdout and "STATUS:error" in proc4.stdout,
    )

    proc5 = _run(_INVALID_ENV_SCRIPT, env_extra={"ESMERELDA_STALE_SYNC_MINUTES": "not-a-number"})
    check("9. Invalid-env-value subprocess exited successfully (no crash on a malformed setting)", proc5.returncode == 0)
    check(
        "10. An invalid ESMERELDA_STALE_SYNC_MINUTES value falls back to the safe default (30) rather than "
        "crashing the whole sync-status/trigger pipeline",
        "THRESHOLD:30" in proc5.stdout,
    )

    proc6 = _run(_INVALID_ENV_SCRIPT, env_extra={"ESMERELDA_STALE_SYNC_MINUTES": "-5"})
    check(
        "10b. A negative ESMERELDA_STALE_SYNC_MINUTES value also falls back to the safe default, not a "
        "nonsensical negative/zero threshold that would immediately mark every sync stale",
        "THRESHOLD:30" in proc6.stdout,
    )
except Exception as exc:
    check(f"0. Configurable-threshold test failed unexpectedly: {exc}", False)


# === 6: concurrent sync requests — the real, atomic fix for the check-then-act race ===
_CONCURRENT_TRIGGER_SCRIPT = """
import threading
from storage.database import init_db, SessionLocal
from storage.models import SyncRun
from storage.crud import try_start_new_sync_run
init_db()

results = []
lock = threading.Lock()

def attempt():
    run = try_start_new_sync_run()
    with lock:
        results.append(run.id if run else None)

threads = [threading.Thread(target=attempt) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()

successes = [r for r in results if r is not None]
print(f"SUCCESS_COUNT:{len(successes)}")
print(f"TOTAL_ATTEMPTS:{len(results)}")

with SessionLocal() as session:
    running_count = session.query(SyncRun).filter(SyncRun.status == "running").count()
    print(f"RUNNING_ROWS_IN_DB:{running_count}")
"""

try:
    proc7 = _run(_CONCURRENT_TRIGGER_SCRIPT)
    check("11. Concurrent-trigger subprocess exited successfully", proc7.returncode == 0)
    if proc7.returncode != 0:
        print(proc7.stdout)
        print(proc7.stderr)
    check(
        "12. CRITICAL: 10 genuinely concurrent (threaded) attempts to start a sync against a clean "
        "database result in EXACTLY ONE success — the atomic INSERT...WHERE NOT EXISTS statement in "
        "try_start_new_sync_run() closes the check-then-act race that a plain "
        "'query, then separately insert' pattern would have left open under real concurrency",
        "SUCCESS_COUNT:1" in proc7.stdout and "TOTAL_ATTEMPTS:10" in proc7.stdout,
    )
    check(
        "13. Exactly one 'running' row exists in the database afterward — not ten competing Playwright "
        "sessions all racing to log into the same Moodle account",
        "RUNNING_ROWS_IN_DB:1" in proc7.stdout,
    )
except Exception as exc:
    check(f"0. Concurrent-trigger test failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
