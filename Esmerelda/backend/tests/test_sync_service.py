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

import http.server
import os
import sys
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
check("4. Due date parsed from timeline text", due is not None and (due.year, due.month, due.day, due.hour, due.minute) == (2026, 9, 20, 11, 59))
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


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
