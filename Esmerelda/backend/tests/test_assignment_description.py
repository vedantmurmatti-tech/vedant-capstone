"""Tests for moodle/sync_service.py's _fetch_assignment_page_details()
(the combined submission-status + description extraction that replaced
_fetch_submission_status()) and storage/crud.py's save_assignment()
description handling.

Investigated before implementing (see BUILD_LOG.md): Assignment.description
and Resource.description already existed in the schema and were already
wired all the way through to the API (AssignmentOut/ResourceOut) and the
frontend's Assignment/Resource TypeScript types — but the sync pipeline
never populated either one. For Assignment, a description IS available at
essentially zero extra cost: _fetch_assignment_page_details() already
navigates to the assignment's own page to read its submission-status table,
so reading its description/intro text from the same already-loaded page
costs no additional network request. For Resource, no such existing page
visit exists — extracting a resource's description would require an
entirely new per-resource page navigation with no current justification,
so Resource.description is deliberately left unpopulated (a reasoned
decision, not an oversight) and this file does not test it.

Description extraction targets Moodle's `#intro` container (mod_assign's
own core view.php template output) — a reasoned choice, NOT verified
against a real, live Moodle instance from this environment (no real
Moodle account was available). These tests exercise the extraction logic
against a controlled, real headless-Chromium-rendered HTML page — proving
the extraction/length-cap/preservation code paths work correctly, not
that this specific selector matches the real lms.flame.edu.in theme
(explicitly not claimed).

Run with:
    venv/Scripts/python.exe tests/test_assignment_description.py
"""

import http.server
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

_REAL_DESCRIPTION = "Submit a 2000-word report analyzing the case study distributed in Week 3."
_LONG_DESCRIPTION = "A" * 5000  # exceeds _MAX_ASSIGNMENT_DESCRIPTION_CHARS (2000)

_ASSIGNMENT_PAGE_WITH_INTRO = f"""
<html><body>
<div id="intro" class="box generalbox boxaligncenter">
  <div class="no-overflow"><p>{_REAL_DESCRIPTION}</p></div>
</div>
<table class="submissionstatustable">
<tr><td>Submission status</td><td>Submitted for grading</td></tr>
</table>
</body></html>
"""

_ASSIGNMENT_PAGE_WITH_LONG_INTRO = f"""
<html><body>
<div id="intro"><p>{_LONG_DESCRIPTION}</p></div>
</body></html>
"""

_ASSIGNMENT_PAGE_NO_INTRO = """
<html><body>
<table class="submissionstatustable">
<tr><td>Submission status</td><td>No submission</td></tr>
</table>
</body></html>
"""

# Mirrors the exact date/time text shape moodle/sync_service.py's Timeline
# extraction already trusts (_DUE_DATE_PATTERN) — this test exercises the
# SAME pattern applied to an assignment's own "Due date" label instead
# (_extract_due_date_from_assignment_page()), the new fix for assignments
# discovered only via the course-page path (which has no Timeline text at
# all to read a due date from).
_ASSIGNMENT_PAGE_WITH_DUE_DATE = """
<html><body>
<div class="assignment-info">
  <table>
    <tr><td>Due date</td><td>Friday, 20 September 2026 11:59</td></tr>
  </table>
</div>
</body></html>
"""

_ASSIGNMENT_PAGE_NO_DUE_DATE = """
<html><body>
<p>Assignment page with no due date label at all.</p>
</body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/with-intro"):
            body = _ASSIGNMENT_PAGE_WITH_INTRO
        elif self.path.startswith("/with-long-intro"):
            body = _ASSIGNMENT_PAGE_WITH_LONG_INTRO
        elif self.path.startswith("/with-due-date"):
            body = _ASSIGNMENT_PAGE_WITH_DUE_DATE
        elif self.path.startswith("/no-due-date"):
            body = _ASSIGNMENT_PAGE_NO_DUE_DATE
        else:
            body = _ASSIGNMENT_PAGE_NO_INTRO
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


_EXTRACTION_SCRIPT = """
from playwright.sync_api import sync_playwright
from moodle.sync_service import _fetch_assignment_page_details, _MAX_ASSIGNMENT_DESCRIPTION_CHARS

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    status1, desc1, due1 = _fetch_assignment_page_details(page, "BASE_URL/with-intro")
    print(f"WITH_INTRO_STATUS:{status1}")
    print(f"WITH_INTRO_DESC:{desc1}")

    status2, desc2, due2 = _fetch_assignment_page_details(page, "BASE_URL/no-intro")
    print(f"NO_INTRO_DESC:{desc2!r}")

    status3, desc3, due3 = _fetch_assignment_page_details(page, "BASE_URL/with-long-intro")
    print(f"LONG_DESC_LEN:{len(desc3) if desc3 else 0}")
    print(f"LONG_DESC_CAPPED:{len(desc3) <= _MAX_ASSIGNMENT_DESCRIPTION_CHARS if desc3 else None}")

    status4, desc4, due4 = _fetch_assignment_page_details(page, "BASE_URL/with-due-date")
    print(f"WITH_DUE_DATE:{due4.isoformat() if due4 else None}")

    status5, desc5, due5 = _fetch_assignment_page_details(page, "BASE_URL/no-due-date")
    print(f"NO_DUE_DATE:{due5!r}")

    context.close()
    browser.close()
"""


def _run_extraction_script(base_url: str) -> subprocess.CompletedProcess:
    script = _EXTRACTION_SCRIPT.replace("BASE_URL", base_url)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )


try:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

    proc = _run_extraction_script(base_url)
    check("1. Extraction subprocess exited successfully", proc.returncode == 0)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)

    check(
        "2. A real assignment page with an #intro container yields both a real submission status and "
        "a real, correctly-extracted description",
        f"WITH_INTRO_STATUS:Submitted for grading" in proc.stdout
        and f"WITH_INTRO_DESC:{_REAL_DESCRIPTION}" in proc.stdout,
    )
    check(
        "3. A page with no #intro container at all yields None for description, not an empty string or "
        "a crash — extraction failure/absence is a safe, distinguishable default",
        "NO_INTRO_DESC:None" in proc.stdout,
    )
    check(
        "4. A description longer than the length cap is truncated to at most "
        "_MAX_ASSIGNMENT_DESCRIPTION_CHARS characters, never stored unbounded",
        "LONG_DESC_LEN:2000" in proc.stdout and "LONG_DESC_CAPPED:True" in proc.stdout,
    )
    check(
        "5. CRITICAL: a real assignment page with a 'Due date' label is correctly parsed into a real "
        "datetime — this is the fix for assignments discovered only via the course-page path, which "
        "previously always stored due_date=NULL (displaying as 'Unknown' in the UI) because it had no "
        "Timeline text to read a due date from at all",
        "WITH_DUE_DATE:2026-09-20T11:59:00" in proc.stdout,
    )
    check(
        "6. A page with no 'Due date' label at all yields None, not a crash or a fabricated date",
        "NO_DUE_DATE:None" in proc.stdout,
    )
except Exception as exc:
    check(f"0. Extraction test setup failed unexpectedly: {exc}", False)
finally:
    try:
        httpd.shutdown()
        server_thread.join(timeout=5)
    except Exception:
        pass


# === 5, 6: save_assignment() preserves an existing description when a later scrape has none ===
def _run_db_script(script: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tempfile.mkdtemp(prefix="esmerelda_assignment_description_test_")
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


_DB_SCRIPT = """
from storage.database import init_db, SessionLocal
from storage.models import Course, Assignment
from storage.crud import save_course, save_assignment
init_db()

save_course(moodle_id="101", name="Test Course")
save_assignment(moodle_id="a1", course_name="Test Course", name="Assignment 1", description="Write a report.")
# A later sync pass that couldn't extract a description (e.g. the #intro
# selector didn't match, or extraction failed for any reason) must not
# erase the previously-known real one.
save_assignment(moodle_id="a1", course_name="Test Course", name="Assignment 1", description=None)

with SessionLocal() as session:
    a = session.query(Assignment).filter(Assignment.moodle_id == "a1").first()
    print(f"DESCRIPTION:{a.description!r}")

# A genuinely new, different description DOES overwrite the old one.
save_assignment(moodle_id="a1", course_name="Test Course", name="Assignment 1", description="Updated instructions.")
with SessionLocal() as session:
    a = session.query(Assignment).filter(Assignment.moodle_id == "a1").first()
    print(f"UPDATED_DESCRIPTION:{a.description!r}")
"""

try:
    proc2 = _run_db_script(_DB_SCRIPT)
    check("7. save_assignment description-preservation subprocess exited successfully", proc2.returncode == 0)
    if proc2.returncode != 0:
        print(proc2.stdout)
        print(proc2.stderr)
    check(
        "8. A later save_assignment() call with description=None does not erase a previously-saved real "
        "description",
        "DESCRIPTION:'Write a report.'" in proc2.stdout,
    )
    check(
        "9. A later save_assignment() call WITH a real, different description correctly updates it — "
        "confirms this isn't a one-way lock, just a None-doesn't-overwrite rule",
        "UPDATED_DESCRIPTION:'Updated instructions.'" in proc2.stdout,
    )
except Exception as exc:
    check(f"0. save_assignment description-preservation test failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
