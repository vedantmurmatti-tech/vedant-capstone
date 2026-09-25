"""Regression test for moodle/sync_service.py's _sync_course_assignments()
(Timeline-block assignment discovery) against the REAL, current FLAME
Moodle dashboard HTML structure — reproduced from a real production log,
not invented (see BUILD_LOG.md's Timeline-discovery entry).

Real production evidence this test reproduces directly:
  - The Timeline block's OWN static filter/sort dropdown menu is exactly
    8 `a.dropdown-item` elements ("All", "Overdue", "Next 7 days",
    "Next 30 days", "Next 3 months", "Next 6 months", "Sort by dates",
    "Sort by courses") — every one `href="#"` or a same-page tab
    fragment (`href="#view_dates_..."`/`href="#view_courses_..."`),
    never `/mod/assign/...`. A real log confirmed the block becomes
    "visible" with ONLY these 8 elements present; the block's real
    event list (the actual assignment links) renders separately and
    asynchronously afterward.
  - The previous fixed `page.wait_for_timeout(1500)` was not reliably
    long enough for that second, async render to finish — this fixture
    deliberately injects the real event content via a delayed script
    (3000ms, LONGER than the old fixed wait, shorter than the new
    explicit `wait_for(state="attached", timeout=8000)`) so this test
    would have caught the exact real regression before it happened, and
    fails again if the wait is ever shortened back below what a real
    async render needs.

Covers:
  - The 8 real dropdown-item filter/sort links never count as discovered
    assignments (correctly rejected, not a crash, not miscounted).
  - A real assignment link that only appears in the DOM after the
    block becomes visible (simulating Moodle's real async event render)
    IS still discovered and persisted — the actual regression fix.
  - A genuinely empty Timeline (no assignment ever appears) does not
    error or hang — it correctly returns zero discovered, exactly as
    before this fix.

Run with:
    venv/Scripts/python.exe tests/test_timeline_async_discovery.py
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


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        print(f"[PASS] {label}")
        passed += 1
    else:
        print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))
        failed += 1


backend_dir = Path(__file__).resolve().parent.parent

_TEST_USERNAME = "timeline_async_student"
_TEST_PASSWORD = "correct-horse-battery-staple"  # test-only, never a real credential
_COURSE_NAME = "TASY101 Timeline Async Course"
_ASSIGNMENT_NAME = "Real Async Assignment"

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


def _dashboard_html(server_port: int) -> str:
    return f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<p>Only courses in progress</p>
<div><a href="http://127.0.0.1:{server_port}/course/view.php?id=401">{_COURSE_NAME}</a></div>
<p>Course overview</p>
</body></html>
"""


# The Timeline block's real dropdown markup, copied verbatim (structure
# and classes/attributes) from a real production Render log — the exact
# 8 candidates that were being found and correctly-but-uselessly
# rejected every single sync before this fix. The real event content
# (the actual assignment link) is injected via a delayed script instead
# of being present at initial render, reproducing the real async-load
# timing that broke the old fixed-wait approach.
_TIMELINE_MY_PAGE_HTML = f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<section class="block_timeline">
  <div class="dropdown">
    <a class="dropdown-item" href="#" data-from="-14" data-filtername="all" aria-label="All filter option" role="menuitem">
        All
    </a>
    <a class="dropdown-item" href="#" data-from="-14" data-to="1" data-filtername="overdue" aria-label="Overdue filter option" role="menuitem">
        Overdue
    </a>
    <a class="dropdown-item" href="#" data-from="0" data-to="7" data-filtername="next7days" aria-current="true" aria-label="Next 7 days filter option" role="menuitem">
        Next 7 days
    </a>
    <a class="dropdown-item" href="#" data-from="0" data-to="30" data-filtername="next30days" aria-label="Next 30 days filter option" role="menuitem">
        Next 30 days
    </a>
    <a class="dropdown-item" href="#" data-from="0" data-to="90" data-filtername="next3months" aria-label="Next 3 months filter option" role="menuitem">
        Next 3 months
    </a>
    <a class="dropdown-item" href="#" data-from="0" data-to="180" data-filtername="next6months" aria-label="Next 6 months filter option" role="menuitem">
        Next 6 months
    </a>
  </div>
  <div class="nav">
    <a class="dropdown-item" href="#view_dates_testregion" data-toggle="tab" data-filtername="sortbydates" aria-current="true" aria-label="Sort by dates sort option" role="menuitem">
        <span>Sort by dates</span>
    </a>
    <a class="dropdown-item" href="#view_courses_testregion" data-toggle="tab" data-filtername="sortbycourses" aria-label="Sort by courses sort option" role="menuitem">
        <span>Sort by courses</span>
    </a>
  </div>
  <div id="view_dates_testregion" class="real-events"></div>
</section>
<script>
// Simulates Moodle's real Timeline block: the filter/sort menu renders
// immediately, but the actual event list is fetched/rendered
// asynchronously afterward — 3000ms here, deliberately LONGER than the
// old fixed 1500ms wait this fix replaced, so this test fails against
// the old code and passes against the new explicit wait.
setTimeout(function() {{
  document.getElementById("view_dates_testregion").innerHTML =
    '<div class="event-name-container">' +
    '<div><a href="/course/view.php?id=401">{_COURSE_NAME}</a></div>' +
    '<small class="text-muted">Friday, 20 September 2026 11:59</small>' +
    '<div><a href="/mod/assign/view.php?id=777" class="font-weight-bold">{_ASSIGNMENT_NAME}</a></div>' +
    '</div>';
}}, 3000);
</script>
</body></html>
"""

# A second Timeline fixture with NO event ever injected — the genuine
# "nothing due" case, which must keep working (no crash, no hang, 0
# discovered) exactly as before this fix.
_TIMELINE_EMPTY_MY_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<section class="block_timeline">
  <div class="dropdown">
    <a class="dropdown-item" href="#" data-filtername="all" role="menuitem">All</a>
    <a class="dropdown-item" href="#" data-filtername="overdue" role="menuitem">Overdue</a>
  </div>
</section>
</body></html>
"""

_COURSE_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<p>No downloadable resources on this course page for this test.</p>
</body></html>
"""

_ASSIGNMENT_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<p>Assignment page — deliberately has no "Due date" text; this test only
cares about Timeline discovery itself, not due-date extraction.</p>
</body></html>
"""

_use_empty_timeline = {"value": False}


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
        if not self._authenticated():
            body = _LOGIN_PAGE_HTML
        elif self.path.startswith("/course/view.php?id=401"):
            body = _COURSE_PAGE_HTML
        elif self.path.startswith("/mod/assign/view.php"):
            body = _ASSIGNMENT_PAGE_HTML
        elif self.path.startswith("/my/") or self.path == "/my":
            body = _TIMELINE_EMPTY_MY_PAGE_HTML if _use_empty_timeline["value"] else _TIMELINE_MY_PAGE_HTML
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
            _valid_sessions.add("tasy-sess-1")
            self.send_response(303)
            self.send_header("Set-Cookie", "session=tasy-sess-1; Path=/")
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(_LOGIN_PAGE_HTML.encode("utf-8"))


_RUN_SYNC_SCRIPT = """
from storage.database import init_db
init_db()

from moodle.sync_service import run_sync
result = run_sync()
print(f"RESULT:{result.courses_discovered}:{result.assignments_discovered}:{result.assignments_synced}")

from storage.database import SessionLocal
from storage.models import Assignment
with SessionLocal() as session:
    assignments = session.query(Assignment).all()
    for a in assignments:
        print(f"ASSIGNMENT\\t{a.moodle_id}\\t{a.name}")
"""


def _run_subprocess_script(env: dict, script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# === Part 1: the real dropdown-menu + delayed-async-event structure ===
try:
    _use_empty_timeline["value"] = False
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    server_port = httpd.server_address[1]
    moodle_url = f"http://127.0.0.1:{server_port}/"

    tmp_data_dir = tempfile.mkdtemp(prefix="esmerelda_timeline_async_test_")
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tmp_data_dir
    env["MOODLE_USERNAME"] = _TEST_USERNAME
    env["MOODLE_PASSWORD"] = _TEST_PASSWORD
    env["MOODLE_URL"] = moodle_url

    proc = _run_subprocess_script(env, _RUN_SYNC_SCRIPT)
    check("1. run_sync() subprocess (real dropdown-menu + delayed async event) exited successfully", proc.returncode == 0)
    if proc.returncode != 0:
        print("--- stdout ---")
        print(proc.stdout)
        print("--- stderr ---")
        print(proc.stderr)

    assignment_lines = [l for l in proc.stdout.splitlines() if l.startswith("ASSIGNMENT\t")]
    names = [line.split("\t", 2)[2] for line in assignment_lines]

    check(
        "2. CRITICAL: the real assignment link, injected into the DOM 3s after the Timeline block became "
        "visible (matching Moodle's real async event-render timing), IS discovered and persisted — this is "
        "the exact regression this fix addresses (the old fixed 1500ms wait would have missed it)",
        _ASSIGNMENT_NAME in names,
        f"discovered assignment names={names}",
    )
    check(
        "3. None of the Timeline block's own 8 real filter/sort dropdown items (All, Overdue, Next 7/30 "
        "days, Next 3/6 months, Sort by dates, Sort by courses) were ever mistaken for an assignment",
        not any(n in names for n in ["All", "Overdue", "Next 7 days", "Next 30 days", "Next 3 months", "Next 6 months", "Sort by dates", "Sort by courses"]),
        f"discovered assignment names={names}",
    )
    check(
        "4. Exactly one real assignment was discovered — not zero (the regression) and not duplicated",
        len(names) == 1,
        f"discovered assignment names={names}",
    )
except Exception as exc:
    check(f"0. Part 1 test setup failed unexpectedly: {exc}", False)
finally:
    try:
        httpd.shutdown()
        server_thread.join(timeout=5)
    except Exception:
        pass


# === Part 2: a genuinely empty Timeline (no assignment ever appears) ===
try:
    _use_empty_timeline["value"] = True
    _valid_sessions.clear()
    httpd2 = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server_thread2 = threading.Thread(target=httpd2.serve_forever, daemon=True)
    server_thread2.start()
    server_port2 = httpd2.server_address[1]
    moodle_url2 = f"http://127.0.0.1:{server_port2}/"

    tmp_data_dir2 = tempfile.mkdtemp(prefix="esmerelda_timeline_empty_test_")
    env2 = dict(os.environ)
    env2["ESMERELDA_DATA_DIR"] = tmp_data_dir2
    env2["MOODLE_USERNAME"] = _TEST_USERNAME
    env2["MOODLE_PASSWORD"] = _TEST_PASSWORD
    env2["MOODLE_URL"] = moodle_url2

    proc2 = _run_subprocess_script(env2, _RUN_SYNC_SCRIPT)
    check("5. run_sync() subprocess (genuinely empty Timeline) exited successfully", proc2.returncode == 0)
    if proc2.returncode != 0:
        print("--- stdout ---")
        print(proc2.stdout)
        print("--- stderr ---")
        print(proc2.stderr)

    assignment_lines2 = [l for l in proc2.stdout.splitlines() if l.startswith("ASSIGNMENT\t")]
    check(
        "6. A genuinely empty Timeline (no event ever rendered) correctly discovers zero assignments — "
        "not a crash, not a hang, not a false positive — the new wait_for() times out gracefully after 8s "
        "exactly as designed",
        len(assignment_lines2) == 0,
        f"got {len(assignment_lines2)} assignment(s): {assignment_lines2}",
    )
except Exception as exc:
    check(f"0. Part 2 test setup failed unexpectedly: {exc}", False)
finally:
    try:
        httpd2.shutdown()
        server_thread2.join(timeout=5)
    except Exception:
        pass


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
