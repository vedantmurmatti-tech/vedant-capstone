"""Tests for the temporary per-sync document-download safety limit
(moodle/document_downloader.py's ESMERELDA_MAX_DOCUMENT_DOWNLOADS_PER_SYNC,
default 3).

Verifies, against a real headless-Chromium-driven run_sync() over a real
local fake Moodle server (not mocks):
  - Only the configured number of eligible resources are actually
    downloaded/processed in one sync, even when more are eligible.
  - The limit applies ONLY to the document-download stage — course
    discovery, resource *metadata* persistence, and assignment discovery
    are completely unaffected and still see/persist every real item.
  - The skip is logged clearly (limit value, resources selected,
    resources skipped because the limit was reached).
  - The limit is configurable via the environment variable.
  - 0 explicitly means unlimited, not "download nothing."

Run with:
    venv/Scripts/python.exe tests/test_document_download_limit.py
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
_RESOURCE_COUNT = 6  # deliberately more than the default limit (3)

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


def _dashboard_html(port: int) -> str:
    return f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
<p>Only courses in progress</p>
<div><a href="http://127.0.0.1:{port}/course/view.php?id=201">DLIM101 Download Limit Test Course</a></div>
<p>Course overview</p>
</body></html>
"""


def _course_page_html() -> str:
    links = "".join(
        f'<a href="/mod/resource/view.php?id={500 + i}">Handout {i}.pdf</a>\n' for i in range(_RESOURCE_COUNT)
    )
    return f"""
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
{links}
</body></html>
"""


_MY_PAGE_HTML = """
<html><body>
<div class="usermenu"><a href="/login/logout.php?sesskey=x">Log out</a></div>
</body></html>
"""

_FILE_CONTENT = b"%PDF-1.4 test content " + bytes(range(50))


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
            body = _LOGIN_PAGE_HTML.encode("utf-8")
        elif self.path.startswith("/course/view.php?id=201"):
            body = _course_page_html().encode("utf-8")
        elif self.path.startswith("/mod/resource/view.php"):
            self.send_response(302)
            self.send_header("Location", f"/pluginfile.php/1/mod_resource/content/1/handout{self.path[-3:]}.pdf")
            self.end_headers()
            return
        elif self.path.startswith("/pluginfile.php"):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(_FILE_CONTENT)))
            self.end_headers()
            self.wfile.write(_FILE_CONTENT)
            return
        elif self.path.startswith("/my/") or self.path == "/my":
            body = _MY_PAGE_HTML.encode("utf-8")
        else:
            body = _dashboard_html(self.server.server_address[1]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        fields = parse_qs(self.rfile.read(length).decode("utf-8"))
        if fields.get("username", [""])[0] == _TEST_USERNAME and fields.get("password", [""])[0] == _TEST_PASSWORD:
            _valid_sessions.add("dlim-sess-1")
            self.send_response(303)
            self.send_header("Set-Cookie", "session=dlim-sess-1; Path=/")
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(_LOGIN_PAGE_HTML.encode("utf-8"))


_RUN_SYNC_SCRIPT = """
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
from storage.database import init_db
init_db()
from moodle.sync_service import run_sync
result = run_sync()
print(f"RESULT:{result.courses_synced}:{result.resources_synced}:{result.documents_eligible}:{result.documents_downloaded}")

from storage.database import SessionLocal
from storage.models import Resource, Document
with SessionLocal() as session:
    print(f"RESOURCE_ROWS:{session.query(Resource).count()}")
    print(f"DOCUMENT_ROWS:{session.query(Document).count()}")
"""


def _run_sync(port: int, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tempfile.mkdtemp(prefix="esmerelda_download_limit_test_")
    env["MOODLE_USERNAME"] = _TEST_USERNAME
    env["MOODLE_PASSWORD"] = _TEST_PASSWORD
    env["MOODLE_URL"] = f"http://127.0.0.1:{port}/"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", _RUN_SYNC_SCRIPT],
        cwd=str(backend_dir), env=env, capture_output=True, text=True, timeout=60,
    )


try:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    port = httpd.server_address[1]

    # === 1: default behavior (env unset) — only 3 of 6 eligible resources downloaded ===
    proc1 = _run_sync(port)
    check("1. Default-limit sync subprocess exited successfully", proc1.returncode == 0)
    if proc1.returncode != 0:
        print(proc1.stdout)
        print(proc1.stderr)
    check(
        f"2. All {_RESOURCE_COUNT} resources are still discovered and persisted at the METADATA level — "
        "the download limit does not affect resource/course discovery at all",
        f"RESOURCE_ROWS:{_RESOURCE_COUNT}" in proc1.stdout,
    )
    check(
        "3. CRITICAL: with no ESMERELDA_MAX_DOCUMENT_DOWNLOADS_PER_SYNC set, only 3 documents are "
        "actually downloaded this sync — the safety default — even though 6 resources were eligible",
        f"RESULT:1:{_RESOURCE_COUNT}:{_RESOURCE_COUNT}:3" in proc1.stdout and "DOCUMENT_ROWS:3" in proc1.stdout,
    )
    combined1 = proc1.stdout + proc1.stderr
    check(
        "4. The download limit itself is logged clearly",
        "document download limit=3" in combined1,
    )
    check(
        "5. Which resources were selected for download this sync is logged",
        "documents selected for download this sync=3" in combined1,
    )
    check(
        "6. Resources skipped specifically because the limit was reached are logged, distinct from a "
        "download failure or an ordinary ineligibility skip",
        "document download limit reached" in combined1 and "3 skipped" in combined1,
    )

    # === 2: configurable via the environment variable ===
    proc2 = _run_sync(port, env_extra={"ESMERELDA_MAX_DOCUMENT_DOWNLOADS_PER_SYNC": "2"})
    check("7. Configured-limit-of-2 sync subprocess exited successfully", proc2.returncode == 0)
    check(
        "8. Setting ESMERELDA_MAX_DOCUMENT_DOWNLOADS_PER_SYNC=2 genuinely limits downloads to 2, not "
        "the hardcoded default of 3",
        f"RESULT:1:{_RESOURCE_COUNT}:{_RESOURCE_COUNT}:2" in proc2.stdout and "DOCUMENT_ROWS:2" in proc2.stdout,
    )

    # === 3: 0 explicitly means unlimited ===
    proc3 = _run_sync(port, env_extra={"ESMERELDA_MAX_DOCUMENT_DOWNLOADS_PER_SYNC": "0"})
    check("9. Unlimited (0) sync subprocess exited successfully", proc3.returncode == 0)
    check(
        f"10. ESMERELDA_MAX_DOCUMENT_DOWNLOADS_PER_SYNC=0 means unlimited — all {_RESOURCE_COUNT} eligible "
        "resources are downloaded, not zero",
        f"RESULT:1:{_RESOURCE_COUNT}:{_RESOURCE_COUNT}:{_RESOURCE_COUNT}" in proc3.stdout
        and f"DOCUMENT_ROWS:{_RESOURCE_COUNT}" in proc3.stdout,
    )
    check(
        "11. The unlimited case is also logged clearly, not silently",
        "document download limit=unlimited" in (proc3.stdout + proc3.stderr),
    )

    # === 4: an invalid env value falls back safely to the default, not a crash ===
    proc4 = _run_sync(port, env_extra={"ESMERELDA_MAX_DOCUMENT_DOWNLOADS_PER_SYNC": "not-a-number"})
    check("12. Invalid-env-value sync subprocess exited successfully (no crash)", proc4.returncode == 0)
    check(
        "13. An invalid limit value falls back to the safe default of 3 rather than crashing the sync",
        f"RESULT:1:{_RESOURCE_COUNT}:{_RESOURCE_COUNT}:3" in proc4.stdout,
    )

    httpd.shutdown()
    server_thread.join(timeout=5)
except Exception as exc:
    check(f"0. Test setup failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
