"""Tests from a Render deployment-readiness audit — main.py's /health
endpoint and startup diagnostics, and api/routes.py's
/api/sync/moodle/diagnostics data-directory reporting.

Root cause investigated: GET /health previously always returned
{"status": "ok"} unconditionally, regardless of whether the database was
actually reachable. On a platform like Render, a health check is what
decides whether traffic keeps being routed to an instance and whether an
unhealthy one gets restarted — a health check that can never fail is
worse than no health check at all, since it would let a genuinely broken
instance (e.g. a permissions problem on a newly-attached Persistent Disk,
or a corrupted database file) keep reporting itself healthy while every
real request fails with a 500.

Does not attempt to test real Render or Moodle behavior — only this
project's own code, run locally.

Run with:
    venv/Scripts/python.exe tests/test_deployment_readiness.py
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


def _run(script: str, env_extra: dict | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tempfile.mkdtemp(prefix="esmerelda_deployment_readiness_test_")
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


# === 1: a healthy database reports 200/ok ===
_HEALTHY_SCRIPT = """
from fastapi.testclient import TestClient
from main import app
client = TestClient(app)
r = client.get("/health")
print(f"STATUS_CODE:{r.status_code}")
print(f"BODY:{r.json()}")
"""

# === 2: a genuinely broken database connection reports 503, not a false 200 ===
_BROKEN_DB_SCRIPT = """
from fastapi.testclient import TestClient
import storage.database as db_module

class _BrokenEngine:
    def connect(self):
        raise RuntimeError("simulated: database file is not accessible")

db_module.engine = _BrokenEngine()

from main import app
client = TestClient(app)
r = client.get("/health")
print(f"STATUS_CODE:{r.status_code}")
print(f"BODY:{r.json()}")
"""

# === 3: startup logs report the resolved data directory and ephemeral-fallback warning ===
_STARTUP_LOG_ONLY_ENV_SCRIPT = """
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
import asyncio
from main import app, lifespan

async def _run():
    async with lifespan(app):
        pass

asyncio.run(_run())
"""

_STARTUP_LOG_WITH_DATA_DIR_SCRIPT = _STARTUP_LOG_ONLY_ENV_SCRIPT  # same script; env differs per call

try:
    proc1 = _run(_HEALTHY_SCRIPT)
    check("1. Healthy-database subprocess exited successfully", proc1.returncode == 0)
    if proc1.returncode != 0:
        print(proc1.stdout)
        print(proc1.stderr)
    check(
        "2. GET /health reports 200/ok when the database is genuinely reachable",
        "STATUS_CODE:200" in proc1.stdout and "'status': 'ok'" in proc1.stdout,
    )
except Exception as exc:
    check(f"0. Healthy-database test failed unexpectedly: {exc}", False)

try:
    proc2 = _run(_BROKEN_DB_SCRIPT)
    check("3. Broken-database subprocess exited successfully (the health check itself doesn't crash the app)", proc2.returncode == 0)
    if proc2.returncode != 0:
        print(proc2.stdout)
        print(proc2.stderr)
    check(
        "4. CRITICAL: GET /health reports 503/error when the database is genuinely unreachable — not a "
        "false 'ok' that would let Render keep routing traffic to a broken instance",
        "STATUS_CODE:503" in proc2.stdout and "'status': 'error'" in proc2.stdout,
    )
    check(
        "5. No secret appears in the health check's error detail (this project's database has no "
        "connection credentials to leak — it's a local SQLite file, not a network service)",
        "MOODLE_" not in proc2.stdout and "PASSWORD" not in proc2.stdout,
    )
except Exception as exc:
    check(f"0. Broken-database test failed unexpectedly: {exc}", False)

try:
    # This scenario specifically requires ESMERELDA_DATA_DIR to be UNSET —
    # _run()'s own default always sets it (every other test in this file
    # needs an isolated temp database), so it's removed here explicitly
    # rather than relying on _run()'s usual behavior. cwd is still the
    # backend directory, so the unset-fallback resolves to backend/storage/
    # (a real directory this test suite is allowed to write to, same as
    # any other local dev run) rather than anything outside the repo.
    env = dict(os.environ)
    env.pop("ESMERELDA_DATA_DIR", None)
    proc3 = subprocess.run(
        [sys.executable, "-c", _STARTUP_LOG_ONLY_ENV_SCRIPT],
        cwd=str(backend_dir), env=env, capture_output=True, text=True, timeout=30,
    )
    check("6. Startup-log (ephemeral fallback) subprocess exited successfully", proc3.returncode == 0)
    if proc3.returncode != 0:
        print(proc3.stdout)
        print(proc3.stderr)
    combined = proc3.stdout + proc3.stderr
    check(
        "7. Startup logs clearly warn when ESMERELDA_DATA_DIR is unset — the exact scenario that would "
        "silently wipe data on every Render restart/redeploy if left unnoticed",
        "ESMERELDA_DATA_DIR is not set" in combined,
    )

    proc4 = _run(_STARTUP_LOG_WITH_DATA_DIR_SCRIPT, env_extra={"ESMERELDA_DATA_DIR": tempfile.mkdtemp(prefix="esmerelda_explicit_data_dir_")})
    check("8. Startup-log (explicit data dir) subprocess exited successfully", proc4.returncode == 0)
    combined4 = proc4.stdout + proc4.stderr
    check(
        "9. Startup logs report the non-ephemeral case distinctly when ESMERELDA_DATA_DIR IS set — a "
        "real, useful positive confirmation, not just a warning for the bad case",
        "ESMERELDA_DATA_DIR is set" in combined4,
    )
except Exception as exc:
    check(f"0. Startup-log test failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
