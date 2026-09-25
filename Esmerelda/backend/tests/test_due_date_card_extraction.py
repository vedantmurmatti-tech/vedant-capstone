"""Tests for moodle/sync_service.py's _extract_card_due_date() — the
course/Timeline CARD-level due-date extraction path added after real
Render sync evidence (see BUILD_LOG.md's due-date-card-extraction entry)
showed most individual assignment pages don't expose "Due date" text in
their own body at all, while FLAME Moodle's real course/Timeline listing
reliably renders a short "Due: <date>" badge directly on the assignment's
own card/list item — confirmed from a real screenshot of the live site
(three real assignments: "Assessment 1: Idea Lock-in, Plan & Repo Setup"
due "Friday, 11 September 2026, 11:59 PM", "Assessment 2: Custom Skill,
an agent, 1-2 MCP, chained into one workflow" due "Thursday, 17 September
2026, 11:59 PM", "Assessment 3: MVP" due "Saturday, 19 September 2026,
11:59 PM").

Part 1 (unit-level, direct calls to _extract_card_due_date against a real
headless-Chromium-rendered page) exercises the requested scenarios
directly and cheaply: a card with no Due text, a card with "Due:" text
but a malformed/unparseable date, multiple assignments on the same page,
and an assignment with a due date next to one without.

Part 1 also demonstrates a REAL bug this project's own testing caught and
fixed (not a hypothetical) — a first version of the ancestor-walk
technique, when it widened past an assignment's own card into a shared
list wrapper, attributed a NEIGHBORING assignment's real due date to one
that genuinely had none. The "exactly one distinct href in scope" guard
in _extract_card_due_date() exists specifically because of this, and
Part 1's multi-card test is what caught it originally.

Part 2 (end-to-end) runs a real run_sync() against a real fake Moodle
course page containing all three of FLAME's real screenshot date/time
strings, verifying they persist to Assignment.due_date correctly
converted to naive-UTC (see storage/timezones.py) — i.e. the exact
values the real Render sync should produce for these three real
assignments once deployed.

Run with:
    venv/Scripts/python.exe tests/test_due_date_card_extraction.py
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

# --- Part 1: unit-level, direct calls to _extract_card_due_date --------

_UNIT_TEST_SCRIPT = """
from playwright.sync_api import sync_playwright
from moodle.sync_service import _extract_card_due_date

html = '''
<html><body>
<div class="activity-list">
  <li class="activity assign modtype_assign">
    <div class="activity-dates">Due: Friday, 11 September 2026, 11:59 PM</div>
    <a href="/mod/assign/view.php?id=101">Assessment 1: Idea Lock-in, Plan &amp; Repo Setup</a>
  </li>
  <li class="activity assign modtype_assign">
    <div class="activity-dates">Opened: Saturday, 19 September 2026, 4:37 AM Due: Saturday, 19 September 2026, 11:59 PM</div>
    <a href="/mod/assign/view.php?id=103">Assessment 3: MVP</a>
  </li>
  <li class="activity assign modtype_assign">
    <a href="/mod/assign/view.php?id=104">Assessment 4: No Due Date Here</a>
  </li>
  <li class="activity assign modtype_assign">
    <div class="activity-dates">Due: whenever, sometime next week</div>
    <a href="/mod/assign/view.php?id=105">Assessment 5: Malformed Due Text</a>
  </li>
</div>
</body></html>
'''

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(html)

    r1 = _extract_card_due_date(
        page.locator('a[href*="id=101"]'),
        assignment_name="Assessment 1: Idea Lock-in, Plan & Repo Setup",
        card_url="id=101",
    )
    print(f"CARD_WITH_DUE_DATE:{r1.isoformat() if r1 else None}")

    r2 = _extract_card_due_date(
        page.locator('a[href*="id=103"]'),
        assignment_name="Assessment 3: MVP",
        card_url="id=103",
    )
    print(f"CARD_WITH_OPENED_AND_DUE:{r2.isoformat() if r2 else None}")

    r3 = _extract_card_due_date(
        page.locator('a[href*="id=104"]'),
        assignment_name="Assessment 4: No Due Date Here",
        card_url="id=104",
    )
    print(f"CARD_WITH_NO_DUE_TEXT:{r3!r}")

    r4 = _extract_card_due_date(
        page.locator('a[href*="id=105"]'),
        assignment_name="Assessment 5: Malformed Due Text",
        card_url="id=105",
    )
    print(f"CARD_WITH_MALFORMED_DUE_TEXT:{r4!r}")

    browser.close()
"""


def _run_unit_script() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _UNIT_TEST_SCRIPT],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )


try:
    proc = _run_unit_script()
    check("1. Unit-level subprocess exited successfully", proc.returncode == 0)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)

    check(
        "2. CRITICAL: a card with a real 'Due:' badge next to the assignment's own link is correctly "
        "parsed — 11:59 PM IST on 11 Sept 2026 -> 18:29 UTC, exactly FLAME's real "
        "'Assessment 1: Idea Lock-in, Plan & Repo Setup' due date from the real screenshot",
        "CARD_WITH_DUE_DATE:2026-09-11T18:29:00" in proc.stdout,
    )
    check(
        "3. A card with BOTH 'Opened:' and 'Due:' badges present correctly picks the 'Due:' one, not "
        "'Opened:' — matches the real 'Assessment 3: MVP' card shape from the screenshot",
        "CARD_WITH_OPENED_AND_DUE:2026-09-19T18:29:00" in proc.stdout,
    )
    check(
        "4. CRITICAL: a card with genuinely no 'Due:' text of its own, sitting NEXT TO a sibling card "
        "that DOES have one, correctly returns None — does NOT steal the neighboring card's due date "
        "(a real bug this project's own testing caught before this guard existed — see BUILD_LOG.md)",
        "CARD_WITH_NO_DUE_TEXT:None" in proc.stdout,
    )
    check(
        "5. A card with 'Due:' text that doesn't match the expected date pattern (malformed/unparseable) "
        "correctly returns None — not a crash, not a guessed date",
        "CARD_WITH_MALFORMED_DUE_TEXT:None" in proc.stdout,
    )
except Exception as exc:
    check(f"0. Unit-level test setup failed unexpectedly: {exc}", False)


# --- Part 2: end-to-end via a real run_sync() against a real fake course page ---

_TEST_USERNAME = "card_due_date_student"
_TEST_PASSWORD = "correct-horse-battery-staple"  # test-only, never a real credential
_COURSE_NAME = "DCARD101 Card Due-Date Course"

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
<div><a href="http://127.0.0.1:{server_port}/course/view.php?id=301">{_COURSE_NAME}</a></div>
<p>Course overview</p>
</body></html>
"""


# The exact three real assignment names + due-date strings from FLAME's
# own live course/Timeline listing (see the real screenshot referenced in
# BUILD_LOG.md), plus a fourth with no due text and a fifth with
# malformed due text, laid out as separate <li> cards the way a real
# Moodle Boost-theme course page's activity list renders them.
_COURSE_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<ul class="activity-list">
  <li class="activity assign modtype_assign">
    <div class="activity-dates">Due: Friday, 11 September 2026, 11:59 PM</div>
    <a href="/mod/assign/view.php?id=701">Assessment 1: Idea Lock-in, Plan &amp; Repo Setup</a>
  </li>
  <li class="activity assign modtype_assign">
    <div class="activity-dates">Due: Thursday, 17 September 2026, 11:59 PM</div>
    <a href="/mod/assign/view.php?id=702">Assessment 2: Custom Skill, an agent, 1-2 MCP, chained into one workflow</a>
  </li>
  <li class="activity assign modtype_assign">
    <div class="activity-dates">Opened: Saturday, 19 September 2026, 4:37 AM Due: Saturday, 19 September 2026, 11:59 PM</div>
    <a href="/mod/assign/view.php?id=703">Assessment 3: MVP</a>
  </li>
  <li class="activity assign modtype_assign">
    <a href="/mod/assign/view.php?id=704">Assessment 4: No Due Date At All</a>
  </li>
  <li class="activity assign modtype_assign">
    <div class="activity-dates">Due: whenever, sometime next week</div>
    <a href="/mod/assign/view.php?id=705">Assessment 5: Malformed Due Text</a>
  </li>
</ul>
</body></html>
"""

_ASSIGNMENT_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<p>Assignment page — deliberately has NO "Due date" text of its own, proving the
card-level extraction (not the individual-page fallback) is what supplies these
assignments' due dates in this test.</p>
</body></html>
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
        if not self._authenticated():
            body = _LOGIN_PAGE_HTML
        elif self.path.startswith("/course/view.php?id=301"):
            body = _COURSE_PAGE_HTML
        elif self.path.startswith("/mod/assign/view.php"):
            body = _ASSIGNMENT_PAGE_HTML
        elif self.path.startswith("/my/") or self.path == "/my":
            body = "<html><body><div class=\"usermenu\"><a href=\"/login/logout.php\">Log out</a></div></body></html>"
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
            _valid_sessions.add("dcard-sess-1")
            self.send_response(303)
            self.send_header("Set-Cookie", "session=dcard-sess-1; Path=/")
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
        due = a.due_date.isoformat() if a.due_date else None
        # Tab-delimited, not colon-delimited — several of this test's real
        # assignment names contain colons ("Assessment 1: Idea Lock-in...")
        # and every non-null due value is an ISO datetime (which itself
        # contains colons), so a colon-based split is not a safe delimiter
        # here the way it is in tests whose fixture names happen not to.
        print(f"ASSIGNMENT\\t{a.moodle_id}\\t{a.name}\\t{due}")
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


try:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    server_port = httpd.server_address[1]
    moodle_url = f"http://127.0.0.1:{server_port}/"

    tmp_data_dir = tempfile.mkdtemp(prefix="esmerelda_card_due_date_test_")
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tmp_data_dir
    env["MOODLE_USERNAME"] = _TEST_USERNAME
    env["MOODLE_PASSWORD"] = _TEST_PASSWORD
    env["MOODLE_URL"] = moodle_url

    proc = _run_subprocess_script(env, _RUN_SYNC_SCRIPT)
    check("6. End-to-end run_sync() subprocess exited successfully", proc.returncode == 0)
    if proc.returncode != 0:
        print("--- stdout ---")
        print(proc.stdout)
        print("--- stderr ---")
        print(proc.stderr)

    assignment_lines = [l for l in proc.stdout.splitlines() if l.startswith("ASSIGNMENT\t")]
    assignments = {}
    for line in assignment_lines:
        _, moodle_id, name, due = line.split("\t", 3)
        due = due if due != "None" else None
        assignments[moodle_id] = (name, due)

    check(
        "7. CRITICAL: 'Assessment 1: Idea Lock-in, Plan & Repo Setup' persists with the real due date "
        "from its own card (Friday 11 Sept 2026, 11:59 PM IST -> 18:29 UTC) — matches FLAME's real "
        "screenshot exactly",
        assignments.get("701") == ("Assessment 1: Idea Lock-in, Plan & Repo Setup", "2026-09-11T18:29:00"),
        f"got {assignments.get('701')}",
    )
    check(
        "8. CRITICAL: 'Assessment 2: Custom Skill, an agent, 1-2 MCP, chained into one workflow' persists "
        "with its real due date (Thursday 17 Sept 2026, 11:59 PM IST -> 18:29 UTC)",
        assignments.get("702") == (
            "Assessment 2: Custom Skill, an agent, 1-2 MCP, chained into one workflow", "2026-09-17T18:29:00"
        ),
        f"got {assignments.get('702')}",
    )
    check(
        "9. CRITICAL: 'Assessment 3: MVP' persists with its real due date (Saturday 19 Sept 2026, 11:59 PM "
        "IST -> 18:29 UTC), correctly picked over the 'Opened:' date also present on the same card",
        assignments.get("703") == ("Assessment 3: MVP", "2026-09-19T18:29:00"),
        f"got {assignments.get('703')}",
    )
    check(
        "10. An assignment with genuinely no 'Due:' text on its card, and no 'Due date' text on its own "
        "page either, correctly persists with due_date=None — not fabricated",
        assignments.get("704") == ("Assessment 4: No Due Date At All", None),
        f"got {assignments.get('704')}",
    )
    check(
        "11. An assignment with a 'Due:' label but unparseable text persists with due_date=None, not a "
        "crash and not a guessed date",
        assignments.get("705") == ("Assessment 5: Malformed Due Text", None),
        f"got {assignments.get('705')}",
    )
    check(
        "12. All 5 real assignments on the same course page were discovered and persisted — multiple "
        "assignments on the same page, each with its own independently correct due date",
        len(assignments) == 5,
        f"got {len(assignments)} assignments: {assignments}",
    )
except Exception as exc:
    check(f"0. End-to-end test setup failed unexpectedly: {exc}", False)
finally:
    try:
        httpd.shutdown()
        server_thread.join(timeout=5)
    except Exception:
        pass


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
