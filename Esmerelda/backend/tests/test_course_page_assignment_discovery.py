"""Tests for the secondary, course-page-based assignment-discovery path
added in moodle/sync_service.py (_scan_page_for_resources()'s assignment-
candidate collection, _sync_course_page_assignments()).

The dashboard Timeline block (the ONLY assignment-discovery path before
this change) only ever surfaces an assignment that has a due date and
currently falls inside Moodle's own Timeline display window. An
assignment with no due date, one further out than the Timeline shows, or
one a particular theme/version simply doesn't render into the Timeline
template at all was previously never discovered by this project's sync
at all, regardless of how many times it ran. This file exercises the fix
against a real, self-contained fake Moodle server (login, dashboard,
course pages, assignment pages) and a real headless-Chromium-driven
run_sync() — not mocks.

Covers:
  - A course-page-only assignment (never in the Timeline) is discovered
    and persisted.
  - An assignment present in BOTH the Timeline and a course page is
    persisted exactly once (no duplicate row, via the shared moodle_id
    dedup key).
  - A malformed assignment link (no parseable id in its href) is skipped
    gracefully, without aborting discovery of any other assignment.
  - A pre-existing due_date is not erased when the course-page path
    (which has no due-date text to scrape) saves the same assignment
    with due_date=None.
  - One active course's page failing to load entirely (simulated via an
    unreachable URL) does not prevent another active course's
    assignments from being discovered via either path.
  - A course NOT listed under "Only courses in progress" is never
    visited at all, and no assignment from it is ever discovered.

Run with:
    venv/Scripts/python.exe tests/test_course_page_assignment_discovery.py
"""

import http.server
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs

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

_TEST_USERNAME = "test_student"
_TEST_PASSWORD = "correct-horse-battery-staple"  # test-only, never a real credential

_COURSE_A_NAME = "CPAD101 Course-Page Discovery Course"
_COURSE_B_NAME = "CPAD102 Inactive Course"
_TIMELINE_ONLY_NAME = "Timeline-Only placeholder"  # unused, kept out of fixtures deliberately
_DUPLICATE_ASSIGNMENT_NAME = "Assignment In Both Places"
_COURSE_PAGE_ONLY_NAME = "Assignment Only On Course Page"
_MALFORMED_ASSIGNMENT_NAME = "Malformed Assignment Link"
_INACTIVE_COURSE_ASSIGNMENT_NAME = "Assignment On An Inactive Course"

_LOGIN_PAGE_HTML = """
<html><body>
<form id="login" action="/login/index.php" method="post">
  <input type="text" id="username" name="username">
  <input type="password" id="password" name="password">
  <button type="submit" id="loginbtn">Log in</button>
</form>
</body></html>
"""

_valid_sessions: set[str] = set()
_course_b_visited = False

# A real, unreachable local address (nothing listens on port 1) — used as
# an active course's URL to simulate one course's page failing to load
# entirely (connection refused), fast and deterministically, without
# needing a real network failure or a slow timeout to reproduce it.
_UNREACHABLE_COURSE_URL = "http://127.0.0.1:1/course/view.php?id=203"


def _dashboard_html(server_port: int) -> str:
    return f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<p>Only courses in progress</p>
<div><a href="http://127.0.0.1:{server_port}/course/view.php?id=201">{_COURSE_A_NAME}</a></div>
<div><a href="{_UNREACHABLE_COURSE_URL}">Unreachable Course C</a></div>
<p>Course overview</p>
<div><a href="http://127.0.0.1:{server_port}/course/view.php?id=202">{_COURSE_B_NAME}</a></div>
</body></html>
"""


_MY_PAGE_HTML = f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<section class="block_timeline">
  <div class="tab-pane active" id="tab-next7days">
    <div class="event-name-container">
      <div><a href="/course/view.php?id=201">{_COURSE_A_NAME}</a></div>
      <small class="text-muted">Friday, 20 September 2026 11:59</small>
      <div><a href="/mod/assign/view.php?id=601" class="font-weight-bold">{_DUPLICATE_ASSIGNMENT_NAME}</a></div>
    </div>
  </div>
</section>
</body></html>
"""

_COURSE_A_PAGE_HTML = f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<a href="/mod/resource/view.php?id=999">Some Resource.pdf</a>
<a href="/mod/assign/view.php?id=601">{_DUPLICATE_ASSIGNMENT_NAME}</a>
<a href="/mod/assign/view.php?id=602">{_COURSE_PAGE_ONLY_NAME}</a>
<a href="/mod/assign/view.php">{_MALFORMED_ASSIGNMENT_NAME}</a>
</body></html>
"""

_COURSE_B_PAGE_HTML = f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<a href="/mod/assign/view.php?id=999">{_INACTIVE_COURSE_ASSIGNMENT_NAME}</a>
</body></html>
"""

_ASSIGNMENT_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<p>Assignment page</p>
</body></html>
"""

_RESOURCE_PAGE_HTML = """
<html><body><p>a real resource, not needed by this test's assertions</p></body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _authenticated(self) -> bool:
        cookie_header = self.headers.get("Cookie", "")
        return any(
            part.strip().startswith("session=") and part.strip().partition("=")[2] in _valid_sessions
            for part in cookie_header.split(";")
        )

    def do_GET(self):
        global _course_b_visited
        if not self._authenticated():
            body = _LOGIN_PAGE_HTML
        elif self.path.startswith("/course/view.php?id=201"):
            body = _COURSE_A_PAGE_HTML
        elif self.path.startswith("/course/view.php?id=202"):
            _course_b_visited = True
            body = _COURSE_B_PAGE_HTML
        elif self.path.startswith("/mod/assign/view.php"):
            body = _ASSIGNMENT_PAGE_HTML
        elif self.path.startswith("/mod/resource/view.php"):
            body = _RESOURCE_PAGE_HTML
        elif self.path.startswith("/my/") or self.path == "/my":
            body = _MY_PAGE_HTML
        else:
            body = _dashboard_html(self.server.server_address[1])
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        fields = parse_qs(self.rfile.read(length).decode("utf-8"))
        if fields.get("username", [""])[0] == _TEST_USERNAME and fields.get("password", [""])[0] == _TEST_PASSWORD:
            _valid_sessions.add("cpad-sess-1")
            self.send_response(303)
            self.send_header("Set-Cookie", "session=cpad-sess-1; Path=/")
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(_LOGIN_PAGE_HTML.encode("utf-8"))


_SEED_SCRIPT = """
from datetime import datetime
from storage.database import init_db, SessionLocal
from storage.models import Course, Assignment
init_db()
with SessionLocal() as session:
    course = Course(moodle_id="201", name="placeholder before first real sync")
    session.add(course)
    session.flush()
    # A course-page-only assignment already has a real due_date from some
    # earlier source (e.g. a prior sync that had a Timeline entry for it
    # before it aged out of the Timeline's display window) — proving the
    # course-page path (which has no due-date text to scrape) does not
    # erase it by saving due_date=None over it.
    assignment = Assignment(
        moodle_id="602", course_id=course.id, name="placeholder",
        due_date=datetime(2026, 1, 1, 12, 0),
    )
    session.add(assignment)
    session.commit()
"""

_RUN_SYNC_SCRIPT = """
from moodle.sync_service import run_sync
result = run_sync()
print(f"RESULT:{result.courses_discovered}:{result.assignments_discovered}:{result.assignments_synced}")

from storage.database import SessionLocal
from storage.models import Assignment
with SessionLocal() as session:
    assignments = session.query(Assignment).all()
    for a in assignments:
        due = a.due_date.isoformat() if a.due_date else None
        print(f"ASSIGNMENT:{a.moodle_id}:{a.name}:{due}")
    print(f"ASSIGNMENT_COUNT:{len(assignments)}")
"""


def _run_subprocess_script(env: dict, script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


try:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    server_port = httpd.server_address[1]
    moodle_url = f"http://127.0.0.1:{server_port}/"

    tmp_data_dir = tempfile.mkdtemp(prefix="esmerelda_course_page_assignments_test_")
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tmp_data_dir
    env["MOODLE_USERNAME"] = _TEST_USERNAME
    env["MOODLE_PASSWORD"] = _TEST_PASSWORD
    env["MOODLE_URL"] = moodle_url

    seed_proc = _run_subprocess_script(env, _SEED_SCRIPT)
    check("0. Seed subprocess exited successfully", seed_proc.returncode == 0)
    if seed_proc.returncode != 0:
        print(seed_proc.stdout)
        print(seed_proc.stderr)

    proc = _run_subprocess_script(env, _RUN_SYNC_SCRIPT, timeout=60)
    check("1. run_sync() subprocess exited successfully", proc.returncode == 0)
    if proc.returncode != 0:
        print("--- stdout ---")
        print(proc.stdout)
        print("--- stderr ---")
        print(proc.stderr)

    assignment_lines = [l for l in proc.stdout.splitlines() if l.startswith("ASSIGNMENT:")]
    assignments = {}
    for line in assignment_lines:
        _, moodle_id, name, due = line.split(":", 3)
        assignments[moodle_id] = (name, due)
    count_line = next((l for l in proc.stdout.splitlines() if l.startswith("ASSIGNMENT_COUNT:")), None)

    check(
        "2. A course-page-only assignment (never in the Timeline) is discovered and persisted",
        "602" in assignments and assignments["602"][0] == _COURSE_PAGE_ONLY_NAME,
    )
    check(
        "3. An assignment present in BOTH the Timeline and the course page is persisted exactly once — "
        "no duplicate row for moodle_id=601",
        count_line == "ASSIGNMENT_COUNT:2",
    )
    check(
        "3b. The deduplicated assignment's real data (from whichever path actually saved it) is present",
        "601" in assignments and assignments["601"][0] == _DUPLICATE_ASSIGNMENT_NAME,
    )
    check(
        "4. The malformed assignment link (no parseable id in its href) was skipped gracefully — "
        "no assignment row exists for it, and it didn't abort discovery of the other two",
        not any(name == _MALFORMED_ASSIGNMENT_NAME for name, _ in assignments.values()),
    )
    check(
        "5. A pre-existing due_date is preserved when the course-page path (no due-date text available) "
        "saves the same assignment with due_date=None — not erased to null",
        "602" in assignments and assignments["602"][1] == "2026-01-01T12:00:00",
    )
    check(
        "6. One active course's page failing to load entirely (unreachable URL) does not prevent another "
        "active course's assignments from being discovered via either path — the sync still completed "
        "and found both real assignments",
        len(assignments) == 2,
    )
    check(
        "7. A course not listed under 'Only courses in progress' is never visited at all",
        not _course_b_visited,
    )
    check(
        "7b. No assignment from the inactive course was ever discovered or persisted",
        "999" not in assignments or assignments.get("999", (None,))[0] != _INACTIVE_COURSE_ASSIGNMENT_NAME,
    )

    httpd.shutdown()
    server_thread.join(timeout=5)
    import shutil
    shutil.rmtree(tmp_data_dir, ignore_errors=True)
except Exception as exc:
    check(f"0. Test setup itself failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
