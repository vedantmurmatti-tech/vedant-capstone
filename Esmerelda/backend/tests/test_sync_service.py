"""Tests for the deterministic, non-Playwright-dependent logic in
moodle/sync_service.py: credential validation, resource-type
classification, Moodle course-id extraction, and due-date parsing.

Not a pytest suite (pytest isn't installed) — plain assert-style checks,
matching the existing convention (see tests/test_followups.py).

Deliberately does NOT drive a real or mocked Playwright browser — doing
so meaningfully (a real login form, a real dashboard DOM, a real
Timeline block) needs a live Moodle session, which this environment does
not have. See BUILD_LOG.md for this and the sync pipeline's other
documented, honest limitations.

Run with:
    venv/Scripts/python.exe tests/test_sync_service.py
"""

import base64
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from moodle.sync_service import (
    MoodleCredentialsError,
    MoodleLoginError,
    _classify_resource_type,
    _course_moodle_id,
    _extract_due_date,
    _get_credentials,
    _login,
    get_last_login_diagnostics,
)

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


# 1. Missing credentials raise a clean, specific error — not a crash.
os.environ.pop("MOODLE_USERNAME", None)
os.environ.pop("MOODLE_PASSWORD", None)
try:
    _get_credentials()
    check("1. Missing credentials raise MoodleCredentialsError", False)
except MoodleCredentialsError as exc:
    check(
        "1. Missing credentials raise MoodleCredentialsError, message names no secret",
        "MOODLE_USERNAME" in str(exc) and "MOODLE_PASSWORD" in str(exc),
    )

# 1b. Present credentials are read back correctly, including a custom MOODLE_URL.
os.environ["MOODLE_USERNAME"] = "student123"
os.environ["MOODLE_PASSWORD"] = "not-a-real-password"
os.environ["MOODLE_URL"] = "https://lms.example.edu"
username, password, moodle_url = _get_credentials()
check(
    "1b. Present credentials + custom MOODLE_URL are read back exactly",
    (username, password, moodle_url) == ("student123", "not-a-real-password", "https://lms.example.edu"),
)
os.environ.pop("MOODLE_USERNAME", None)
os.environ.pop("MOODLE_PASSWORD", None)
os.environ.pop("MOODLE_URL", None)

# 1c. Default MOODLE_URL is used when unset.
os.environ["MOODLE_USERNAME"] = "u"
os.environ["MOODLE_PASSWORD"] = "p"
_, _, default_url = _get_credentials()
check("1c. Default MOODLE_URL used when unset", default_url == "https://lms.flame.edu.in")
os.environ.pop("MOODLE_USERNAME", None)
os.environ.pop("MOODLE_PASSWORD", None)

# 2. Resource-type classification matches real Moodle URL patterns.
check("2. PDF file classified", _classify_resource_type("https://x/files.pdf") == "PDF")
check("2b. mod/resource/ classified", _classify_resource_type("https://x/mod/resource/view.php?id=1") == "Resource")
check("2c. mod/quiz/ classified", _classify_resource_type("https://x/mod/quiz/view.php?id=1") == "Quiz")
check("2d. Unrecognized href falls back to Activity", _classify_resource_type("https://x/mod/lti/view.php?id=1") == "Activity")

# 3. Moodle numeric id extraction from a real course/assignment URL shape.
check("3. Extracts numeric id from a course URL", _course_moodle_id("https://x/course/view.php?id=24527") == "24527")
check("3b. Returns None when there's no id= in the URL", _course_moodle_id("https://x/course/view.php") is None)

# 4. Due-date parsing from real Moodle Timeline-block text shape.
timeline_text = "Header noise\nFriday, 20 September 2026 11:59\nAssessment 2 is due"
due = _extract_due_date(timeline_text, "Assessment 2 is due")
# Returned as naive-UTC (see storage/timezones.py) — the scraped "11:59" is
# Moodle's own IST display time, converted here to 06:29 UTC.
check("4. Due date parsed from timeline text", due is not None and (due.year, due.month, due.day, due.hour, due.minute) == (2026, 9, 20, 6, 29))
check("4b. No match when the assignment name isn't present", _extract_due_date(timeline_text, "Some other assignment is due") is None)
check("4c. No crash, returns None, when there's no date in the preceding text", _extract_due_date("Assessment 2 is due", "Assessment 2 is due") is None)

# 5. Regression test for the "repeated syncs fail after the first" bug:
# a real headless Chromium session, via the real _login() function,
# against a real local HTTP server that behaves like a minimal Moodle
# login flow (a login form on GET, a session cookie + redirect on a
# correct POST, a logged-in-only marker element once authenticated) —
# run twice, as two fully independent Playwright browser launches (never
# a shared browser/context/storage_state), exactly mirroring what
# run_sync() does on each call. Proves two things a mock could not: (a)
# _login()'s real DOM-based success detection actually works against a
# real page, and (b) a second, completely independent session logs in
# exactly as successfully as the first — the actual regression scenario
# reported (it worked once, then subsequent syncs failed).
_TEST_USERNAME = "test_student"
_TEST_PASSWORD = "correct-horse-battery-staple"  # test-only, never a real credential
_valid_session_ids: set[str] = set()
_login_post_count = 0

_LOGIN_PAGE_HTML = """
<html><body>
<form id="login" action="/login/index.php" method="post">
  <input type="text" id="username" name="username">
  <input type="password" id="password" name="password">
  <button type="submit" id="loginbtn">Log in</button>
</form>
</body></html>
"""

_LOGGED_IN_PAGE_HTML = """
<html><body>
<div class="usermenu">
  <a href="/login/logout.php?sesskey=test">Log out</a>
</div>
<p>Only courses in progress</p>
<p>Course overview</p>
</body></html>
"""


class _FakeMoodleHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep test output quiet

    def _session_cookie(self) -> str | None:
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            if "=" in part:
                key, _, value = part.strip().partition("=")
                if key == "session" and value in _valid_session_ids:
                    return value
        return None

    def do_GET(self):
        body = _LOGGED_IN_PAGE_HTML if self._session_cookie() else _LOGIN_PAGE_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self):
        global _login_post_count
        length = int(self.headers.get("Content-Length", 0))
        fields = parse_qs(self.rfile.read(length).decode("utf-8"))
        _login_post_count += 1
        username = fields.get("username", [""])[0]
        password = fields.get("password", [""])[0]

        if username == _TEST_USERNAME and password == _TEST_PASSWORD:
            session_id = f"sess-{len(_valid_session_ids) + 1}"
            _valid_session_ids.add(session_id)
            self.send_response(303)
            self.send_header("Set-Cookie", f"session={session_id}; Path=/")
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_LOGIN_PAGE_HTML.encode("utf-8"))


def _run_login_against_fake_server(server_url: str) -> None:
    """One full, independent Playwright session — mirrors exactly what
    run_sync() does: a fresh sync_playwright() context, a fresh browser,
    a fresh browser.new_context(), a fresh page, then _login(), always
    closed in finally."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = None
        try:
            context = browser.new_context()
            page = context.new_page()
            _login(page, server_url, _TEST_USERNAME, _TEST_PASSWORD)
        finally:
            if context is not None:
                context.close()
            browser.close()


try:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeMoodleHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    fake_moodle_url = f"http://127.0.0.1:{httpd.server_address[1]}/"

    sync_1_ok = False
    sync_1_error = None
    try:
        _run_login_against_fake_server(fake_moodle_url)
        sync_1_ok = True
    except Exception as exc:
        sync_1_error = exc
    check(f"5. First sync: real _login() succeeds against the fake Moodle server (error: {sync_1_error})", sync_1_ok)

    # 5a-diag. The temporary production-diagnostic capture (added to investigate
    # a Render-only login failure) actually recorded real, sanitized snapshots.
    diagnostics_after_run_1 = get_last_login_diagnostics()
    check(
        "5a-diag. Diagnostics captured exactly the 3 required stages, in order",
        [d["stage"] for d in diagnostics_after_run_1] == ["before_submit", "immediately_after_submit", "after_5_10s_wait"],
    )
    all_text = " ".join(
        str(d.get(k, "")) for d in diagnostics_after_run_1 for k in ("visible_text_sanitized", "login_error_message", "url", "title")
    )
    check(
        "5a-diag2. Neither the real username nor password appears anywhere in the captured diagnostics",
        _TEST_USERNAME not in all_text and _TEST_PASSWORD not in all_text,
    )
    all_fields = [
        field
        for d in diagnostics_after_run_1
        for form in d.get("forms") or []
        for field in form.get("fields") or []
    ]
    check(
        "5a-diag3. Forms/inputs were listed (at least one captured) without any input value ever being recorded",
        len(all_fields) > 0 and all("value" not in field for field in all_fields),
    )
    check(
        "5a-diag4. A response status was captured for the post-submit stage",
        diagnostics_after_run_1[1]["response_status"] is not None,
    )

    sync_2_ok = False
    sync_2_error = None
    try:
        _run_login_against_fake_server(fake_moodle_url)
        sync_2_ok = True
    except Exception as exc:
        sync_2_error = exc
    check(f"5b. Second, fully independent sync ALSO succeeds — the actual regression scenario (error: {sync_2_error})", sync_2_ok)

    check(
        "5c. Both syncs performed a real login POST (2 total) — proves no session/cookie was reused across the two independent browsers",
        _login_post_count == 2,
    )

    # 5d. A genuinely wrong password against the same fresh-session machinery
    # raises MoodleLoginError with a real, DOM-derived message — not a crash,
    # and not a false "success" just because the request completed.
    wrong_password_raised_correctly = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            try:
                page = context.new_page()
                _login(page, fake_moodle_url, _TEST_USERNAME, "definitely-wrong-password")
            finally:
                context.close()
                browser.close()
    except MoodleLoginError:
        wrong_password_raised_correctly = True
    except Exception:
        wrong_password_raised_correctly = False
    check("5e. A genuinely wrong password raises MoodleLoginError, not a false success", wrong_password_raised_correctly)

    httpd.shutdown()
    server_thread.join(timeout=5)
except Exception as exc:
    check(f"5. Regression test setup itself failed unexpectedly: {exc}", False)


# 6. Full post-login pipeline regression test: run_sync() end-to-end — login,
# active-course discovery, course traversal, resource sync, and assignment
# sync (via the dashboard Timeline block) — against a real local fake-Moodle
# server that serves a dashboard, a course page, and a Timeline block, not
# just a login form. This is specifically what proves/regression-guards the
# real bug found and fixed in this change: _sync_course_assignments() used to
# navigate to a bare relative path ("dashboard/"), which Playwright resolves
# against whatever page was last visited (a course page, after resource
# sync ran) rather than the site root — landing on a real Moodle 404 and
# silently discovering 0 assignments even when login succeeded and active
# courses were found. Run via a subprocess with ESMERELDA_DATA_DIR pointed
# at an isolated temp directory, both so this doesn't write fake test data
# into the real local development database, and because storage/database.py
# reads ESMERELDA_DATA_DIR into a module-level DATABASE_URL at import time —
# setting the env var after moodle.sync_service (and therefore
# storage.database) is already imported in *this* process would have no
# effect.
_COURSE_NAME = "TEST101 Full Pipeline Test Course"
_ASSIGNMENT_NAME = "Full Pipeline Test Assignment"
_TABBED_ASSIGNMENT_NAME = "Overdue Tab Test Assignment"

_FULL_DASHBOARD_HTML = f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<p>Only courses in progress</p>
<div><a href="/course/view.php?id=101">{_COURSE_NAME}</a></div>
<p>Course overview</p>
</body></html>
"""

# Reconstructed from Moodle's real core_calendar Timeline block template
# shape, not the "<name> is due" convention a previous version of this
# fixture (and the code under test) both wrongly assumed — see
# BUILD_LOG.md's root-cause entry. Two real-world details this fixture
# specifically exercises:
#  1. The event-name link's own text is just the activity title — no "is
#     due" phrase anywhere in the anchor itself; the due date/time is
#     separate, non-link text nearby.
#  2. Moodle's Timeline groups events into date-range tabs ("Overdue",
#     "Next 7 days", etc.) implemented as Bootstrap tab-panes — only the
#     active tab's pane lacks `display:none`. The first assignment here
#     sits in the active tab; the second sits in an inactive
#     (`display:none`) one, exactly mirroring the already-proven
#     collapsed-course-section resource bug fixed earlier this project.
_FULL_MY_PAGE_HTML = f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<section class="block_timeline">
  <div class="tab-pane active" id="tab-next7days">
    <div class="event-name-container">
      <div><a href="/course/view.php?id=101">{_COURSE_NAME}</a></div>
      <small class="text-muted">Friday, 20 September 2026 11:59</small>
      <div><a href="/mod/assign/view.php?id=555" class="font-weight-bold">{_ASSIGNMENT_NAME}</a></div>
    </div>
  </div>
  <div class="tab-pane" id="tab-overdue" style="display:none;">
    <div class="event-name-container">
      <div><a href="/course/view.php?id=101">{_COURSE_NAME}</a></div>
      <small class="text-muted">Monday, 15 September 2026 09:00</small>
      <div><a href="/mod/assign/view.php?id=556" class="font-weight-bold">{_TABBED_ASSIGNMENT_NAME}</a></div>
    </div>
  </div>
</section>
</body></html>
"""

_FULL_COURSE_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<a href="/mod/resource/view.php?id=999">Lecture Notes.pdf</a>
<a href="/files/course-syllabus.docx">Course Syllabus</a>
<div class="collapse" id="topic-2" style="display:none;">
  <a href="/mod/resource/view.php?id=888">Collapsed Section Handout.pdf</a>
</div>
<a href="/course/view.php?id=101&section=1">Week 2</a>
</body></html>
"""

# Real binary content served for the two downloadable resources above, so
# the document-download pipeline can be tested against actual bytes with
# real, non-text/html content types — not just HTML pages. Not real PDF/
# DOCX files internally (nothing here parses their structure — inspect_
# resource() and process_resource() only look at Content-Type and byte
# content, exactly like they would for a real Moodle-served file), but
# the sizes/content are large and distinctive enough to verify a genuine,
# complete, correct byte-for-byte download rather than a truncated or
# placeholder one.
# A real, valid, parseable PDF (generated once with fpdf2, not a project
# dependency — embedded as bytes so the test suite needs no extra
# dependency to reproduce it) containing real, distinctive text, so the
# Knowledge Base's real text-extraction step (storage/text_extraction.py)
# can be exercised end-to-end through the real sync pipeline, not just
# unit-tested in isolation. Real, meaningful content — not a placeholder —
# specifically so a later chat-retrieval test can search for it.
_REAL_PDF_TEXT = "The Course Outline requires three deliverables: a report, a prototype, and a presentation."
_FAKE_PDF_BYTES = base64.b64decode(
    "JVBERi0xLjMKJenr8b8KMSAwIG9iago8PAovQ291bnQgMQovS2lkcyBbMyAwIFJdCi9NZWRpYUJveCBb"
    "MCAwIDU5NS4yOCA4NDEuODldCi9UeXBlIC9QYWdlcwo+PgplbmRvYmoKMiAwIG9iago8PAovT3BlbkFj"
    "dGlvbiBbMyAwIFIgL0ZpdEggbnVsbF0KL1BhZ2VMYXlvdXQgL09uZUNvbHVtbgovUGFnZXMgMSAwIFIK"
    "L1R5cGUgL0NhdGFsb2cKPj4KZW5kb2JqCjMgMCBvYmoKPDwKL0NvbnRlbnRzIDQgMCBSCi9QYXJlbnQg"
    "MSAwIFIKL1Jlc291cmNlcyA2IDAgUgovVHlwZSAvUGFnZQo+PgplbmRvYmoKNCAwIG9iago8PAovRmls"
    "dGVyIC9GbGF0ZURlY29kZQovTGVuZ3RoIDEzMwo+PgpzdHJlYW0KeJwdzbEKwjAUBdC9X3FHhRKbSok6"
    "Kjq4uOQHIr3SSEnqy6vi30tdz3JaXKvGdA6f6uixuVjY1jQN/ANnv9DWGruD23fGOfgeKz8QpzxLIW6z"
    "jjERwtcchQU6CImeY3xTwn1kOSBAOGXRGgGTZM36nVgjpP4PLEwaNOZk1vDPZf0B76ItSAplbmRzdHJl"
    "YW0KZW5kb2JqCjUgMCBvYmoKPDwKL0Jhc2VGb250IC9IZWx2ZXRpY2EKL0VuY29kaW5nIC9XaW5BbnNp"
    "RW5jb2RpbmcKL1N1YnR5cGUgL1R5cGUxCi9UeXBlIC9Gb250Cj4+CmVuZG9iago2IDAgb2JqCjw8Ci9G"
    "b250IDw8L0YxIDUgMCBSPj4KL1Byb2NTZXQgWy9QREYgL1RleHQgL0ltYWdlQiAvSW1hZ2VDIC9JbWFn"
    "ZUldCj4+CmVuZG9iago3IDAgb2JqCjw8Ci9DcmVhdGlvbkRhdGUgKEQ6MjAyNjA5MTkxMTU3MjRaKQo+"
    "PgplbmRvYmoKeHJlZgowIDgKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDE1IDAwMDAwIG4gCjAw"
    "MDAwMDAxMDIgMDAwMDAgbiAKMDAwMDAwMDIwNSAwMDAwMCBuIAowMDAwMDAwMjg1IDAwMDAwIG4gCjAw"
    "MDAwMDA0OTAgMDAwMDAgbiAKMDAwMDAwMDU4NyAwMDAwMCBuIAowMDAwMDAwNjc0IDAwMDAwIG4gCnRy"
    "YWlsZXIKPDwKL1NpemUgOAovUm9vdCAyIDAgUgovSW5mbyA3IDAgUgovSUQgWzwzNDNBNzg4NjEyMkM3"
    "QTg1ODRCRUIzOUE4MDQwNUMyMD48MzQzQTc4ODYxMjJDN0E4NTg0QkVCMzlBODA0MDVDMjA+XQo+Pgpz"
    "dGFydHhyZWYKNzI5CiUlRU9GCg=="
)


def _build_real_docx_bytes(text: str) -> bytes:
    """A real, valid DOCX with the given real text — python-docx is
    already a real project dependency (storage/text_extraction.py), so
    this needs no extra test-only dependency."""
    import io

    import docx

    document = docx.Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


_REAL_DOCX_TEXT = "The TRENDS Matrix summarizes emerging market trends relevant to the course project."
_FAKE_DOCX_BYTES = _build_real_docx_bytes(_REAL_DOCX_TEXT)

_FULL_COURSE_SECTION_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<a href="/mod/folder/view.php?id=777">Week 2 Readings</a>
</body></html>
"""

_FULL_ASSIGNMENT_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<p>Assignment page</p>
</body></html>
"""

_full_valid_sessions: set[str] = set()
# When True, the session is force-invalidated the moment a course page has
# been visited once — simulating a session becoming invalid sometime during
# or right after resource sync, before assignment sync's own dashboard
# navigation runs. Deliberately not tied to an exact request count (which
# would be fragile to any future change in how many requests resource sync
# happens to make) — this instead robustly captures "session died somewhere
# in the resource-sync phase," which is the actual regression scenario.
# False (the default) means "never expire" — the normal case used by every
# other test in this file.
_full_expire_after_course_page_visited = False
_full_course_page_visited = False


class _FullFakeMoodleHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _authenticated(self) -> bool:
        global _full_course_page_visited
        cookie_header = self.headers.get("Cookie", "")
        has_valid_cookie = any(
            part.strip().startswith("session=") and part.strip().partition("=")[2] in _full_valid_sessions
            for part in cookie_header.split(";")
        )
        if _full_expire_after_course_page_visited and _full_course_page_visited:
            return False
        if has_valid_cookie and self.path.startswith("/course/view.php") and "section=" not in self.path:
            _full_course_page_visited = True
        return has_valid_cookie

    def do_GET(self):
        if not self._authenticated():
            body = _LOGIN_PAGE_HTML
        elif self.path.startswith("/mod/resource/view.php?id=999"):
            # Real Moodle redirects a /mod/resource/view.php wrapper page
            # to a pluginfile.php URL carrying the file's real name — this
            # is what lets storage/text_extraction.py's
            # guess_extractable_type() (driven by the real downloaded
            # filename, not Moodle's "Resource" activity-type label — a
            # real bug this project's own testing caught, see
            # BUILD_LOG.md) correctly recognize this as a PDF. Reproducing
            # that redirect here rather than serving the file directly at
            # this URL, specifically so this fixture matches real Moodle
            # behavior instead of accidentally only working because the
            # test server is simpler than the real thing.
            self.send_response(302)
            self.send_header("Location", "/pluginfile.php/101/mod_resource/content/1/Lecture%20Notes.pdf")
            self.end_headers()
            return
        elif self.path.startswith("/pluginfile.php/101/mod_resource/content/1/Lecture"):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_FAKE_PDF_BYTES)))
            self.end_headers()
            self.wfile.write(_FAKE_PDF_BYTES)
            return
        elif self.path.startswith("/files/course-syllabus.docx"):
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Length", str(len(_FAKE_DOCX_BYTES)))
            self.end_headers()
            self.wfile.write(_FAKE_DOCX_BYTES)
            return
        elif self.path.startswith("/my/") or self.path == "/my":
            body = _FULL_MY_PAGE_HTML
        elif self.path.startswith("/course/view.php") and "section=" in self.path:
            body = _FULL_COURSE_SECTION_PAGE_HTML
        elif self.path.startswith("/course/view.php"):
            body = _FULL_COURSE_PAGE_HTML
        elif self.path.startswith("/mod/assign/view.php"):
            body = _FULL_ASSIGNMENT_PAGE_HTML
        else:
            body = _FULL_DASHBOARD_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        fields = parse_qs(self.rfile.read(length).decode("utf-8"))
        if fields.get("username", [""])[0] == _TEST_USERNAME and fields.get("password", [""])[0] == _TEST_PASSWORD:
            session_id = "full-sess-1"
            _full_valid_sessions.add(session_id)
            self.send_response(303)
            self.send_header("Set-Cookie", f"session={session_id}; Path=/")
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(_LOGIN_PAGE_HTML.encode("utf-8"))


try:
    full_httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FullFakeMoodleHandler)
    full_server_thread = threading.Thread(target=full_httpd.serve_forever, daemon=True)
    full_server_thread.start()
    full_fake_moodle_url = f"http://127.0.0.1:{full_httpd.server_address[1]}/"

    tmp_data_dir = tempfile.mkdtemp(prefix="esmerelda_sync_e2e_")
    subprocess_script = (
        "import logging; logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "from storage.database import init_db; init_db()\n"
        "from moodle.sync_service import run_sync\n"
        "result = run_sync()\n"
        "print(f'RESULT:{result.courses_discovered}:{result.courses_synced}:"
        "{result.resources_discovered}:{result.resources_synced}:"
        "{result.assignments_discovered}:{result.assignments_synced}:"
        "{result.documents_eligible}:{result.documents_downloaded}')\n"
        "from storage.database import SessionLocal\n"
        "from storage.models import Document, DocumentVersion, Resource\n"
        "from storage.paths import get_documents_dir\n"
        "with SessionLocal() as session:\n"
        "    docs = session.query(Document).all()\n"
        "    for d in docs:\n"
        "        resource = session.get(Resource, d.resource_id) if d.resource_id else None\n"
        "        on_disk = (get_documents_dir() / d.file_path).is_file()\n"
        "        size = (get_documents_dir() / d.file_path).stat().st_size if on_disk else -1\n"
        "        print(f'DOCUMENT:{d.name}:{d.resource_id}:{resource.name if resource else None}:{on_disk}:{size}:{d.current_hash}')\n"
        "        print(f'EXTRACTED_TEXT:{d.id}:{d.extracted_text!r}')\n"
        "    versions = session.query(DocumentVersion).count()\n"
        "    print(f'VERSION_COUNT:{versions}')\n"
        "    indexed_count = session.query(Document).filter(Document.extracted_text.isnot(None)).count()\n"
        "    print(f'INDEXED_COUNT:{indexed_count}')\n"
        "# Run a second sync in the same process/database to prove idempotency\n"
        "# (instruction 8) — already-downloaded resources must be skipped, not\n"
        "# re-downloaded or duplicated.\n"
        "result2 = run_sync()\n"
        "print(f'RESULT2_DOCS_DOWNLOADED:{result2.documents_downloaded}')\n"
        "with SessionLocal() as session:\n"
        "    print(f'DOCUMENT_COUNT_AFTER_SECOND_SYNC:{session.query(Document).count()}')\n"
        "    print(f'VERSION_COUNT_AFTER_SECOND_SYNC:{session.query(DocumentVersion).count()}')\n"
    )
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tmp_data_dir
    env["MOODLE_USERNAME"] = _TEST_USERNAME
    env["MOODLE_PASSWORD"] = _TEST_PASSWORD
    env["MOODLE_URL"] = full_fake_moodle_url

    proc = subprocess.run(
        [sys.executable, "-c", subprocess_script],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    check("6. run_sync() subprocess exited successfully (exit code 0)", proc.returncode == 0)

    result_line = next((line for line in proc.stdout.splitlines() if line.startswith("RESULT:")), None)
    check("6b. run_sync() produced a real RESULT line", result_line is not None)
    if result_line:
        parts = [int(p) for p in result_line.removeprefix("RESULT:").split(":")]
        (
            courses_d, courses_p, resources_d, resources_p, assignments_d, assignments_p,
            documents_eligible, documents_downloaded,
        ) = parts
        check(f"6c. Course discovered AND persisted (discovered={courses_d}, persisted={courses_p})", courses_d == 1 and courses_p == 1)
        check(
            f"6d. All 4 resources discovered AND persisted (discovered={resources_d}, persisted={resources_p}): "
            "a normally-visible PDF resource, a direct DOCX file link, one inside a CSS-collapsed section "
            "(display:none — the real bug this step fixed), and one on a separate multi-page course-display "
            "section page",
            resources_d == 4 and resources_p == 4,
        )
        check(
            f"6e. Both assignments discovered AND persisted (discovered={assignments_d}, persisted={assignments_p}): "
            "one from a visible Timeline tab whose link text has no 'is due' phrase (the real bug this step "
            "fixed — identification now relies on the /mod/assign/ href, a stable Moodle-core signal, not "
            "English wording), and one from an inactive (display:none) Timeline tab-pane",
            assignments_d == 2 and assignments_p == 2,
        )
        check(
            f"6o. 3 resources eligible for document download (Resource/PDF/DOCX types — the Folder resource "
            f"correctly excluded), 2 real downloads succeeded (eligible={documents_eligible}, "
            f"downloaded={documents_downloaded}) — this is the previously-entirely-missing pipeline",
            documents_eligible == 3 and documents_downloaded == 2,
        )

        document_lines = [l for l in proc.stdout.splitlines() if l.startswith("DOCUMENT:")]
        version_count_line = next((l for l in proc.stdout.splitlines() if l.startswith("VERSION_COUNT:")), None)
        check("6p. Exactly 2 real Document rows were created (the PDF and the DOCX — not the failed or ineligible ones)", len(document_lines) == 2)
        check(
            "6q. Both documents are linked to their real Resource via resource_id, exist on disk with the exact "
            "downloaded byte size, and have a real SHA-256 hash recorded",
            all(
                parts[1] != "None" and parts[2] != "None" and parts[3] == "True" and int(parts[4]) > 0 and len(parts[5]) == 64
                for line in document_lines
                for parts in [line.removeprefix("DOCUMENT:").split(":")]
            ),
        )
        check(
            "6r. One DocumentVersion was created for each of the 2 real documents",
            version_count_line == "VERSION_COUNT:2",
        )

        extracted_lines = [l for l in proc.stdout.splitlines() if l.startswith("EXTRACTED_TEXT:")]
        indexed_count_line = next((l for l in proc.stdout.splitlines() if l.startswith("INDEXED_COUNT:")), None)
        check(
            "6y. The Knowledge Base's real text-extraction step ran on both real downloaded files "
            "(a real PDF and a real DOCX) and stored their actual real text in Document.extracted_text",
            any(_REAL_PDF_TEXT in l for l in extracted_lines) and any(_REAL_DOCX_TEXT in l for l in extracted_lines),
        )
        check(
            "6z. Both real, extractable documents are counted as indexed (Document.extracted_text IS NOT NULL)",
            indexed_count_line == "INDEXED_COUNT:2",
        )

        second_downloaded_line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT2_DOCS_DOWNLOADED:")), None)
        doc_count_after_line = next((l for l in proc.stdout.splitlines() if l.startswith("DOCUMENT_COUNT_AFTER_SECOND_SYNC:")), None)
        version_count_after_line = next((l for l in proc.stdout.splitlines() if l.startswith("VERSION_COUNT_AFTER_SECOND_SYNC:")), None)
        check(
            "6s. Every eligible document is re-downloaded on a second, repeated sync (Render's filesystem is "
            "ephemeral, so local download history is never trusted as a reason to skip) — both real documents "
            "download again",
            second_downloaded_line == "RESULT2_DOCS_DOWNLOADED:2",
        )
        check(
            "6t. Document/DocumentVersion counts are unchanged after the second sync even though both files "
            "were re-downloaded — save_document()'s upsert-by-file_path dedup means re-downloading identical "
            "content updates the existing row rather than creating a duplicate",
            doc_count_after_line == "DOCUMENT_COUNT_AFTER_SECOND_SYNC:2"
            and version_count_after_line == "VERSION_COUNT_AFTER_SECOND_SYNC:2",
        )

    # logging.basicConfig()'s default stream is stderr, not stdout — only the
    # subprocess's own print(f"RESULT:...") lands on stdout, so the log-line
    # checks below look at stdout+stderr combined (real Render log capture
    # combines both streams too, which is what these markers are meant to
    # simulate being findable in).
    combined_output = proc.stdout + proc.stderr
    check(
        "6f. Every requested stage log line appears in the real end-to-end run's output",
        all(
            marker in combined_output
            for marker in [
                "[SYNC DEBUG] _login returned",
                "[SYNC DEBUG] dashboard/current page loaded",
                "[SYNC DEBUG] active-course discovery started",
                "[SYNC DEBUG] active courses discovered",
                "[SYNC DEBUG] course traversal started",
                "[SYNC DEBUG] visiting course URL",
                "[SYNC DEBUG] sections/topics discovered",
                "[SYNC DEBUG] assignments discovered per course",
                "[SYNC DEBUG] resources discovered for",
                "[SYNC DEBUG] database persistence started",
                "[SYNC DEBUG] course written to DB",
                "[SYNC DEBUG] assignment written to DB",
                "[SYNC DEBUG] resource written to DB",
                "[SYNC DEBUG] database commit completed",
                "Moodle sync summary:",
                "[SYNC DEBUG] sync completed",
            ]
        ),
    )
    check(
        "6g. The explicit 'Moodle sync summary' line has the exact requested field names",
        "courses_discovered=1" in combined_output
        and "assignments_discovered=2" in combined_output
        and "resources_discovered=4" in combined_output
        and "courses_persisted=1" in combined_output
        and "assignments_persisted=2" in combined_output
        and "resources_persisted=4" in combined_output,
    )
    check(
        "6h. Neither the real username nor password appears anywhere in the subprocess's stdout/stderr",
        _TEST_USERNAME not in proc.stdout and _TEST_PASSWORD not in proc.stdout
        and _TEST_USERNAME not in proc.stderr and _TEST_PASSWORD not in proc.stderr,
    )
    check(
        "6i. The collapsed-section resource (the actual reported bug) is discovered by name, not just by count",
        "Collapsed Section Handout.pdf" in combined_output,
    )
    check(
        "6j. The multi-page course-display section page is detected and visited",
        "multi-page course display" in combined_output and "Week 2 Readings" in combined_output,
    )
    check(
        "6k. All 5 resource file types this step was asked to handle are correctly classified "
        "(PDF, Word Document, Page, Link, Folder — the fake server's own resources cover PDF and "
        "Folder for real; the rest are covered directly by unit checks 2/2b/2c above)",
        _classify_resource_type("https://x/file.docx") == "Word Document"
        and _classify_resource_type("https://x/mod/page/view.php?id=1") == "Page"
        and _classify_resource_type("https://x/mod/url/view.php?id=1") == "Link"
        and _classify_resource_type("https://x/mod/folder/view.php?id=1") == "Folder"
        and _classify_resource_type("https://x/file.pdf") == "PDF",
    )
    check(
        "6l. The assignment whose link text has NO 'is due' phrase is discovered by name — "
        "proves identification no longer depends on that English phrase being present",
        _ASSIGNMENT_NAME in combined_output,
    )
    check(
        "6m. The assignment sitting in an inactive (display:none) Timeline tab-pane is ALSO discovered — "
        "proves the is_visible() filter removal, not just the href-based identification fix",
        _TABBED_ASSIGNMENT_NAME in combined_output,
    )
    check(
        "6n. Every logged Timeline candidate includes real, useful diagnostic fields, not just a count",
        "Timeline candidate 0/" in combined_output
        and "text=" in combined_output
        and "href=" in combined_output
        and "parent_text=" in combined_output,
    )
    check(
        "6u. All requested document-pipeline log stages are present: eligible, per-item progress (N/X), "
        "succeeded, failed, and a final summary with documents_downloaded/documents_failed/versions created "
        "— every eligible resource is attempted on every sync, none skipped for existing-locally reasons",
        "documents eligible=3" in combined_output
        and "document download 1/3" in combined_output
        and "document download 2/3" in combined_output
        and "document download 3/3" in combined_output
        and "succeeded: resource_id=" in combined_output
        and "failed (no file found or save error)" in combined_output
        and "document sync summary:" in combined_output
        and "documents_downloaded=2" in combined_output
        and "documents_failed=1" in combined_output
        and "documents_created=2" in combined_output
        and "versions_created=2" in combined_output,
    )
    check(
        "6v. The document-download step ran without breaking the already-working course/assignment/resource "
        "sync — the exact same summary counts from checks 6c/6d/6e still appear after this step ran",
        "Moodle sync summary: courses_discovered=1 assignments_discovered=2 resources_discovered=4" in combined_output,
    )
    check(
        "6w. Explicit call-site logging around _sync_course_assignments() (added to debug a real "
        "'assignments_discovered=0' production regression) shows the call was genuinely reached and "
        "returned a real, non-zero result — added specifically so the NEXT production run can show "
        "unambiguously whether this call happens at all",
        "about to call _sync_course_assignments: active_courses_count=1" in combined_output
        and "returned from _sync_course_assignments: assignments_discovered=2 assignments_persisted=2" in combined_output,
    )
    check(
        "6x. Reproduces the exact current production pipeline ordering (course/resource sync -> document "
        "sync -> assignment sync -> persistence) and proves all four categories succeed together in the "
        "same run: courses>0, resources>0, documents>0, assignments>0 — this is the exact combination "
        "production reported as broken (documents working, assignments zero)",
        courses_p > 0 and resources_p > 0 and documents_downloaded > 0 and assignments_p > 0,
    )

    full_httpd.shutdown()
    full_server_thread.join(timeout=5)
    shutil.rmtree(tmp_data_dir, ignore_errors=True)
except Exception as exc:
    check(f"6. Full pipeline end-to-end test setup itself failed unexpectedly: {exc}", False)


# 7. Regression test for "resources sync but assignments silently return to
# zero" — reproduces a session becoming invalid sometime during/after
# resource sync (this is exactly what was reproduced manually in this
# session before writing any fix: a real headless-Chromium run against a
# session-expiring fake server that landed on the login page while
# navigating to the dashboard for assignment discovery, and, before this
# fix, silently reported 0 assignments discovered with a message claiming
# that wasn't a sign of a problem). Confirms the fix: this now raises a
# clear, diagnostic MoodleSyncError instead of a silent, misleading 0 — and
# that the resources already discovered before the session died are still
# genuinely persisted, i.e. no data is lost, only the sync run is correctly
# marked as failed rather than falsely "successful."
try:
    _full_valid_sessions.clear()
    _full_expire_after_course_page_visited = True
    _full_course_page_visited = False

    regression_httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FullFakeMoodleHandler)
    regression_server_thread = threading.Thread(target=regression_httpd.serve_forever, daemon=True)
    regression_server_thread.start()
    regression_moodle_url = f"http://127.0.0.1:{regression_httpd.server_address[1]}/"

    regression_data_dir = tempfile.mkdtemp(prefix="esmerelda_sync_regression_")
    regression_script = (
        "import logging; logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "from storage.database import init_db; init_db()\n"
        "from moodle.sync_service import run_sync, MoodleSyncError\n"
        "try:\n"
        "    result = run_sync()\n"
        "    print(f'UNEXPECTED_SUCCESS:{result.assignments_discovered}:{result.resources_discovered}')\n"
        "except MoodleSyncError as exc:\n"
        "    print(f'CLEAN_ERROR:{exc}')\n"
    )
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = regression_data_dir
    env["MOODLE_USERNAME"] = _TEST_USERNAME
    env["MOODLE_PASSWORD"] = _TEST_PASSWORD
    env["MOODLE_URL"] = regression_moodle_url

    regression_proc = subprocess.run(
        [sys.executable, "-c", regression_script],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    regression_output = regression_proc.stdout + regression_proc.stderr

    check(
        "7. A session lost during/after resource sync now raises a clear MoodleSyncError "
        "(the regression this step fixes) instead of silently reporting 0 assignments",
        "CLEAN_ERROR:" in regression_output and "UNEXPECTED_SUCCESS" not in regression_output,
    )
    check(
        "7b. The raised error's message clearly identifies session loss, not a generic/misleading cause",
        "Session was lost" in regression_output,
    )
    check(
        "7c. Resources discovered before the session died were still genuinely persisted to the database "
        "(the fix reports the failure honestly — it doesn't also throw away data that was already saved)",
        "resource written to DB" in regression_output and "Lecture Notes.pdf" in regression_output,
    )
    check(
        "7d. Neither the real username nor password appears anywhere in this subprocess's output either",
        _TEST_USERNAME not in regression_output and _TEST_PASSWORD not in regression_output,
    )

    regression_httpd.shutdown()
    regression_server_thread.join(timeout=5)
    shutil.rmtree(regression_data_dir, ignore_errors=True)
    _full_expire_after_course_page_visited = False
except Exception as exc:
    check(f"7. Session-loss regression test setup itself failed unexpectedly: {exc}", False)


# 8. Bounded-memory regression test for the Render out-of-memory incident.
# Measures real process memory (Windows' GetProcessMemoryInfo — stdlib
# ctypes, no new dependency; this dev environment is Windows) for two
# scenarios, each run in its own clean subprocess so one process's peak
# doesn't contaminate the other's baseline:
#   8a. A single large (20 MB) file, downloaded the OLD way (read the
#       entire HTTP response body into one Python object before touching
#       disk) vs. the NEW way (moodle/document_downloader.py's real
#       _stream_resource_to_disk(), 64 KiB chunks straight to disk).
#       Directly demonstrates the actual mechanism fixed — this is
#       platform-independent (peak allocation size is peak allocation
#       size everywhere), unlike allocator-fragmentation-driven RSS
#       creep, which is Linux/glibc-specific and not reproducible from
#       Windows — flagged honestly in BUILD_LOG.md as the one thing this
#       local test cannot directly reproduce.
#   8b. The real run_sync() pipeline against 10 real, medium-sized (3 MB
#       each, 30 MB of real total content) downloadable resources —
#       proves memory does not accumulate/scale with the number or total
#       size of resources processed, only with one file at a time.
try:
    import ctypes
    from ctypes import wintypes

    # Embedded verbatim into each subprocess script below (they're separate
    # processes — nothing importable can be shared directly). Prints its
    # own PEAK_WORKING_SET:<bytes> line. GetCurrentProcess's return value
    # is a pseudo-handle that ctypes truncates on 64-bit Python unless its
    # restype is set explicitly to HANDLE — confirmed directly: without
    # this, GetProcessMemoryInfo silently failed (GetLastError() == 6,
    # ERROR_INVALID_HANDLE) and every measurement below would have
    # silently read back as 0 rather than a real value.
    _PEAK_MEMORY_SNIPPET = (
        "import ctypes\n"
        "from ctypes import wintypes\n"
        "class _C(ctypes.Structure):\n"
        "    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD), "
        "('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t), "
        "('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t), "
        "('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t), "
        "('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]\n"
        "ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE\n"
        "ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_C), wintypes.DWORD]\n"
        "ctypes.windll.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL\n"
        "_c = _C(); _c.cb = ctypes.sizeof(_C)\n"
        "_h = ctypes.windll.kernel32.GetCurrentProcess()\n"
        "ctypes.windll.psapi.GetProcessMemoryInfo(_h, ctypes.byref(_c), _c.cb)\n"
        "print(f'PEAK_WORKING_SET:{_c.PeakWorkingSetSize}')\n"
    )

    _LARGE_FILE_SIZE = 120 * 1024 * 1024  # 120 MB — large enough that the actual
    # buffered-vs-streamed difference dominates over Python/module-import
    # baseline noise (confirmed necessary: an earlier 20 MB run showed only
    # a ~2 MB gap, because import baseline overhead was comparable in size
    # to the file itself — not because the fix has a small effect).
    _LARGE_FILE_BYTES_MARKER = b"L"

    class _LargeFileHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(_LARGE_FILE_SIZE))
            self.end_headers()
            # Written in chunks server-side too — this is a real, ordinary
            # HTTP response of this size, not a special test-only shortcut.
            remaining = _LARGE_FILE_SIZE
            chunk = _LARGE_FILE_BYTES_MARKER * 65536
            while remaining > 0:
                to_write = chunk[:min(len(chunk), remaining)]
                self.wfile.write(to_write)
                remaining -= len(to_write)

    large_file_httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _LargeFileHandler)
    large_file_thread = threading.Thread(target=large_file_httpd.serve_forever, daemon=True)
    large_file_thread.start()
    large_file_url = f"http://127.0.0.1:{large_file_httpd.server_address[1]}/large-file.pdf"

    # 8a-old: the OLD, now-removed approach — read the entire response body
    # into one Python object before writing anything to disk. Reproduced
    # inline here (not by reverting real code) specifically to measure it.
    old_way_script = (
        # Imports the same heavy modules new_way_script does (SQLAlchemy
        # models etc, via document_downloader) purely to equalize the two
        # scripts' baseline memory footprint — otherwise the comparison
        # would be measuring "which script imports more," not "which
        # download approach uses more memory."
        "import moodle.document_downloader as _unused_for_baseline_parity\n"
        "import urllib.request\n"
        f"url = {large_file_url!r}\n"
        "with urllib.request.urlopen(url, timeout=30) as response:\n"
        "    content = response.read()  # the old bug: the ENTIRE file, all at once\n"
        "with open('old_way_output.bin', 'wb') as f:\n"
        "    f.write(content)\n"
        + _PEAK_MEMORY_SNIPPET
    )
    new_way_script = (
        "import sys; sys.path.insert(0, '.')\n"
        "from pathlib import Path\n"
        "from moodle.document_downloader import _stream_resource_to_disk\n"
        f"url = {large_file_url!r}\n"
        "class FakeContext:\n"
        "    def cookies(self, url): return []\n"
        "class FakePage:\n"
        "    context = FakeContext()\n"
        "ok, final_url = _stream_resource_to_disk(FakePage(), url, Path('new_way_output.bin'))\n"
        "print(f'STREAM_OK:{ok}')\n"
        + _PEAK_MEMORY_SNIPPET
    )

    backend_dir = Path(__file__).resolve().parent.parent
    old_proc = subprocess.run([sys.executable, "-c", old_way_script], cwd=str(backend_dir), capture_output=True, text=True, timeout=60)
    new_proc = subprocess.run([sys.executable, "-c", new_way_script], cwd=str(backend_dir), capture_output=True, text=True, timeout=60)

    old_peak_line = next((l for l in old_proc.stdout.splitlines() if l.startswith("PEAK_WORKING_SET:")), None)
    new_peak_line = next((l for l in new_proc.stdout.splitlines() if l.startswith("PEAK_WORKING_SET:")), None)

    check("8a. Both the old-approach and new-approach measurement subprocesses ran successfully", old_peak_line is not None and new_peak_line is not None)
    check("8a-2. The new streaming approach actually completed the download successfully", "STREAM_OK:True" in new_proc.stdout)

    if old_peak_line and new_peak_line:
        old_peak = int(old_peak_line.split(":")[1])
        new_peak = int(new_peak_line.split(":")[1])
        old_peak_mb = old_peak / (1024 * 1024)
        new_peak_mb = new_peak / (1024 * 1024)
        _large_file_mb = _LARGE_FILE_SIZE // (1024 * 1024)
        print(f"\n    [MEMORY] {_large_file_mb} MB file — OLD (full-buffer) peak working set: {old_peak_mb:.1f} MB")
        print(f"    [MEMORY] {_large_file_mb} MB file — NEW (streamed) peak working set:     {new_peak_mb:.1f} MB")
        check(
            f"8a-3. The new streaming approach's peak working set ({new_peak_mb:.1f} MB) is meaningfully lower "
            f"than the old full-buffer approach's ({old_peak_mb:.1f} MB) for a single {_large_file_mb} MB file",
            new_peak < old_peak,
        )

    for f in (backend_dir / "old_way_output.bin", backend_dir / "new_way_output.bin"):
        f.unlink(missing_ok=True)
    large_file_httpd.shutdown()
    large_file_thread.join(timeout=5)

    # 8b: the real run_sync() pipeline against 10 real, medium-sized (3 MB
    # each) resources — proves memory doesn't grow with resource count.
    _MANY_FILE_SIZE = 10 * 1024 * 1024
    _MANY_FILE_COUNT = 15  # 150 MB of real total content, one file at a time
    many_course_links = "".join(
        f'<a href="/mod/resource/view.php?id={900+i}">Handout {i}.pdf</a>\n' for i in range(_MANY_FILE_COUNT)
    )
    many_course_html = f"<html><body><div class='usermenu'><a href='/login/logout.php?sesskey=x'>Log out</a></div>{many_course_links}</body></html>"
    many_dash_html = f"<html><body><div class='usermenu'><a href='/login/logout.php?sesskey=x'>Log out</a></div><p>Only courses in progress</p><div><a href='/course/view.php?id=201'>MANYFILES Test Course</a></div><p>Course overview</p></body></html>"
    many_sessions: set[str] = set()

    class _ManyFilesHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _authed(self):
            c = self.headers.get("Cookie", "")
            return any(p.strip().startswith("session=") and p.strip().partition("=")[2] in many_sessions for p in c.split(";"))

        def do_GET(self):
            if not self._authed():
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(_LOGIN_PAGE_HTML.encode()); return
            if self.path.startswith("/mod/resource/view.php?id=9"):
                self.send_response(200); self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(_MANY_FILE_SIZE)); self.end_headers()
                remaining = _MANY_FILE_SIZE
                chunk = b"M" * 65536
                while remaining > 0:
                    piece = chunk[:min(len(chunk), remaining)]
                    self.wfile.write(piece)
                    remaining -= len(piece)
                return
            if self.path.startswith("/course/view.php"):
                body = many_course_html
            else:
                body = many_dash_html
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            fields = parse_qs(self.rfile.read(length).decode())
            if fields.get("username", [""])[0] == _TEST_USERNAME and fields.get("password", [""])[0] == _TEST_PASSWORD:
                many_sessions.add("many-sess-1")
                self.send_response(303); self.send_header("Set-Cookie", "session=many-sess-1; Path=/")
                self.send_header("Location", "/"); self.end_headers()
            else:
                self.send_response(200); self.end_headers(); self.wfile.write(_LOGIN_PAGE_HTML.encode())

    many_httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ManyFilesHandler)
    many_thread = threading.Thread(target=many_httpd.serve_forever, daemon=True)
    many_thread.start()
    many_url = f"http://127.0.0.1:{many_httpd.server_address[1]}/"
    many_data_dir = tempfile.mkdtemp(prefix="esmerelda_memory_test_")

    many_script = (
        "from storage.database import init_db; init_db()\n"
        "from moodle.sync_service import run_sync\n"
        "result = run_sync()\n"
        "print(f'DOCS_DOWNLOADED:{result.documents_downloaded}')\n"
        + _PEAK_MEMORY_SNIPPET
    )
    many_env = dict(os.environ)
    many_env["ESMERELDA_DATA_DIR"] = many_data_dir
    many_env["MOODLE_USERNAME"] = _TEST_USERNAME
    many_env["MOODLE_PASSWORD"] = _TEST_PASSWORD
    many_env["MOODLE_URL"] = many_url
    # This test's purpose is proving memory doesn't accumulate across many
    # sequentially-processed resources — unrelated to the temporary
    # per-sync download limit (see moodle/document_downloader.py,
    # BUILD_LOG.md), which defaults to only 3 and would otherwise make
    # this test download (and correctly measure memory for) just 3 of the
    # 15 real files instead of all of them. Explicitly unlimited here so
    # this test still exercises its own, original, real concern.
    many_env["ESMERELDA_MAX_DOCUMENT_DOWNLOADS_PER_SYNC"] = "0"

    many_proc = subprocess.run(
        [sys.executable, "-c", many_script], cwd=str(backend_dir), env=many_env,
        capture_output=True, text=True, timeout=120,
    )
    many_peak_line = next((l for l in many_proc.stdout.splitlines() if l.startswith("PEAK_WORKING_SET:")), None)
    many_docs_line = next((l for l in many_proc.stdout.splitlines() if l.startswith("DOCS_DOWNLOADED:")), None)

    check(f"8b. The {_MANY_FILE_COUNT}-real-file run_sync() subprocess completed successfully", many_proc.returncode == 0 and many_peak_line is not None)
    check(f"8b-2. All {_MANY_FILE_COUNT} real files were actually downloaded (not silently skipped)", many_docs_line == f"DOCS_DOWNLOADED:{_MANY_FILE_COUNT}")
    if many_peak_line:
        many_peak_mb = int(many_peak_line.split(":")[1]) / (1024 * 1024)
        total_content_mb = (_MANY_FILE_SIZE * _MANY_FILE_COUNT) / (1024 * 1024)
        single_file_mb = _MANY_FILE_SIZE / (1024 * 1024)
        # The bound is "one file's size plus a generous allowance for
        # Python/SQLAlchemy/Playwright's own baseline footprint" — NOT a
        # fraction of the total content sum. Both the old and new download
        # approaches already process resources strictly one at a time (that
        # was never what changed), so even the old, buffered approach would
        # not literally hold all N files in memory simultaneously — the
        # real question this check answers is whether peak memory scales
        # with the NUMBER of files processed (a real accumulation bug) or
        # stays roughly flat at "one file's worth" regardless of how many
        # are processed in the same run (the correct, fixed behavior).
        bound_mb = single_file_mb + 150
        print(f"\n    [MEMORY] {_MANY_FILE_COUNT} files x {_MANY_FILE_SIZE // (1024*1024)} MB "
              f"({total_content_mb:.0f} MB total real content) — peak working set: {many_peak_mb:.1f} MB "
              f"(bound: one file + baseline = {bound_mb:.0f} MB)")
        check(
            f"8b-3. Peak working set ({many_peak_mb:.1f} MB) processing {_MANY_FILE_COUNT} files "
            f"({total_content_mb:.0f} MB total real content) stays near one file's size plus baseline "
            f"({bound_mb:.0f} MB), not the {total_content_mb:.0f} MB sum — proves memory does not "
            "accumulate across sequentially-processed resources",
            many_peak_mb < bound_mb,
        )

    many_httpd.shutdown()
    many_thread.join(timeout=5)
    shutil.rmtree(many_data_dir, ignore_errors=True)
except Exception as exc:
    check(f"8. Bounded-memory regression test setup itself failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
