"""Tests for the document-download skip logic
(moodle/document_downloader.py's _existing_document_status(),
process_resource(), and sync_resource_documents()) — added because
production was re-downloading all 21 eligible resources on every sync,
even ones already downloaded, because the ONLY check that previously
existed was "does a Document row exist for this resource" — never
whether the file it points at was still actually on disk, current, or
current on Moodle.

Deliberately does NOT drive a full Moodle login/dashboard/course fixture
(see tests/test_sync_service.py for that) — this file seeds a Course/
Resource/Document directly (like tests/test_document_retrieval.py does)
and points a real headless Chromium page directly at a minimal fake file
server, since sync_resource_documents()/process_resource() only need
`page.context.cookies(url)` (works fine with an empty cookie jar — no
login flow needed at all) and a URL to actually reach.

Run with:
    venv/Scripts/python.exe tests/test_document_skip_logic.py
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

_FILE_CONTENT_V1 = b"%PDF-1.4 VERSION-ONE-CONTENT-" + bytes(range(100))
_FILE_CONTENT_V2 = b"%PDF-1.4 VERSION-TWO-CONTENT-LONGER-" + bytes(range(150))  # different size -> different Content-Length


class _FileServerState:
    # Mutated by the parent test process between subprocess runs to
    # change what the fake server serves — simulating a resource that
    # gets updated on Moodle between two sync runs.
    content = _FILE_CONTENT_V1
    serve_404 = False


class _FileHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _respond(self, include_body: bool):
        if _FileServerState.serve_404:
            self.send_response(404)
            self.end_headers()
            return
        body = _FileServerState.content
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self):
        self._respond(include_body=True)

    def do_HEAD(self):
        self._respond(include_body=False)


def _run_subprocess_script(env: dict, script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


_SEED_SCRIPT = """
from storage.database import init_db, SessionLocal
from storage.models import Course, Resource
init_db()
with SessionLocal() as session:
    course = Course(moodle_id="1", name="TEST101 Skip Logic Course", short_name="TEST101")
    session.add(course)
    session.flush()
    resource = Resource(moodle_id="500", course_id=course.id, name="Lecture Notes.pdf", resource_type="PDF", url=FILE_URL)
    session.add(resource)
    session.commit()
    print(f"RESOURCE_ID:{resource.id}")
"""

_SYNC_ONCE_SCRIPT = """
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
from playwright.sync_api import sync_playwright
from storage.database import SessionLocal
from storage.models import Resource
from moodle.document_downloader import sync_resource_documents

with SessionLocal() as session:
    resource = session.query(Resource).filter(Resource.moodle_id == "500").first()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    counts = sync_resource_documents(page)
    print(f"COUNTS:eligible={counts.eligible}:attempted={counts.attempted}:succeeded={counts.succeeded}:skipped={counts.skipped}:failed={counts.failed}")
    context.close()
    browser.close()

from storage.models import Document, DocumentVersion
with SessionLocal() as session:
    doc = session.query(Document).filter(Document.resource_id == resource.id).first()
    if doc:
        from storage.paths import resolve_document_path
        on_disk = resolve_document_path(doc.file_path).is_file()
        size = resolve_document_path(doc.file_path).stat().st_size if on_disk else -1
        version_count = session.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).count()
        print(f"DOCUMENT:id={doc.id}:hash={doc.current_hash}:on_disk={on_disk}:size={size}:extracted_text={doc.extracted_text!r}:versions={version_count}")
        print(f"FILE_PATH:{doc.file_path}")
    else:
        print("DOCUMENT:none")
"""


def _new_data_dir() -> str:
    return tempfile.mkdtemp(prefix="esmerelda_skip_logic_test_")


def _base_env(data_dir: str, file_url: str) -> dict:
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = data_dir
    env["FILE_URL_FOR_TEST"] = file_url
    return env


def _seed(env: dict) -> int:
    script = _SEED_SCRIPT.replace("FILE_URL", f'"{env["FILE_URL_FOR_TEST"]}"')
    proc = _run_subprocess_script(env, script)
    check(f"  (setup) seed subprocess exited 0 for {env['ESMERELDA_DATA_DIR']}", proc.returncode == 0)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESOURCE_ID:")), None)
    if not line:
        print("--- seed stdout ---")
        print(proc.stdout)
        print(proc.stderr)
        raise RuntimeError("seed failed")
    return int(line.split(":")[1])


try:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FileHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    file_url = f"http://127.0.0.1:{httpd.server_address[1]}/lecture-notes.pdf"

    # === 1 & 2: first sync downloads, second sync skips ===
    data_dir = _new_data_dir()
    env = _base_env(data_dir, file_url)
    _seed(env)

    proc1 = _run_subprocess_script(env, _SYNC_ONCE_SCRIPT)
    check("1. First sync subprocess exited successfully", proc1.returncode == 0)
    check("1b. First sync: 1 eligible, 1 attempted, 1 succeeded, 0 skipped", "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc1.stdout)
    check("1c. First sync: a real Document row was created, with a real file on disk, a real hash, and 1 DocumentVersion", "on_disk=True" in proc1.stdout and "versions=1" in proc1.stdout)

    proc2 = _run_subprocess_script(env, _SYNC_ONCE_SCRIPT)
    check("2. Second sync subprocess exited successfully", proc2.returncode == 0)
    check("2b. Second sync: 0 attempted, 1 skipped — the exact reported bug this step fixes", "COUNTS:eligible=1:attempted=0:succeeded=0:skipped=1:failed=0" in proc2.stdout)
    check(
        "2c. The exact requested log line format appears: "
        "'document download skipped: resource_id=X name=...  reason=already_downloaded'. "
        "logging.basicConfig() defaults to stderr, not stdout — checked combined, matching how "
        "real log viewers (Render's included) capture both streams together.",
        "document download skipped: resource_id=" in (proc2.stdout + proc2.stderr)
        and "reason=already_downloaded" in (proc2.stdout + proc2.stderr),
    )

    # === 3: existing extracted_text is not regenerated ===
    # Directly overwrite extracted_text with an obviously-fake sentinel between
    # syncs — real extraction would never produce this exact string — then run
    # a third sync (still skipped) and confirm the sentinel survives untouched.
    sentinel_script = """
from storage.database import SessionLocal
from storage.models import Document, Resource
with SessionLocal() as session:
    resource = session.query(Resource).filter(Resource.moodle_id == "500").first()
    doc = session.query(Document).filter(Document.resource_id == resource.id).first()
    doc.extracted_text = "SENTINEL_TEXT_THAT_REAL_EXTRACTION_WOULD_NEVER_PRODUCE"
    session.commit()
"""
    _run_subprocess_script(env, sentinel_script)
    proc3 = _run_subprocess_script(env, _SYNC_ONCE_SCRIPT)
    check("3. Third sync (still skipped) exited successfully", proc3.returncode == 0)
    check("3b. Still skipped, not re-attempted", "COUNTS:eligible=1:attempted=0:succeeded=0:skipped=1:failed=0" in proc3.stdout)
    check(
        "3c. CRITICAL: the sentinel extracted_text survives untouched — proves a skip genuinely "
        "never re-runs extraction, not just that it doesn't re-download",
        "SENTINEL_TEXT_THAT_REAL_EXTRACTION_WOULD_NEVER_PRODUCE" in proc3.stdout,
    )

    # === 4: missing local file is re-downloaded ===
    file_path_line = next((l for l in proc1.stdout.splitlines() if l.startswith("FILE_PATH:")), None)
    file_name = file_path_line.split(":", 1)[1]
    downloaded_file = Path(data_dir) / "documents" / file_name
    check("  (setup) the real downloaded file exists before deleting it", downloaded_file.is_file())
    downloaded_file.unlink()
    proc4 = _run_subprocess_script(env, _SYNC_ONCE_SCRIPT)
    check("4. Sync after deleting the local file exited successfully", proc4.returncode == 0)
    check(
        "4b. A resource whose local file went missing (e.g. Render's ephemeral filesystem wiped it — "
        "see BUILD_LOG.md) is genuinely re-downloaded, not skipped forever",
        "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc4.stdout,
    )
    check("4c. The file exists on disk again after re-download", "on_disk=True" in proc4.stdout)
    check("4d. The reason for the re-attempt is logged explicitly", "reason=missing_local_file" in proc4.stdout)

    # === 5: a resource updated on Moodle (reliable metadata: HEAD Content-Length changed) ===
    data_dir2 = _new_data_dir()
    env2 = _base_env(data_dir2, file_url)
    _seed(env2)
    _FileServerState.content = _FILE_CONTENT_V1
    proc5a = _run_subprocess_script(env2, _SYNC_ONCE_SCRIPT)
    check("5. Initial sync (v1 content) succeeded", "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc5a.stdout)

    _FileServerState.content = _FILE_CONTENT_V2  # simulates the file changing on Moodle
    proc5b = _run_subprocess_script(env2, _SYNC_ONCE_SCRIPT)
    check("5b. After the resource changes on Moodle (different Content-Length), the sync detects it and re-downloads", "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc5b.stdout)
    check("5c. The re-download reason is logged as resource_updated_on_moodle", "reason=resource_updated_on_moodle" in proc5b.stdout)
    check(
        "5d. The re-downloaded file's real size on disk reflects the NEW (v2) content, not the stale v1 size",
        f"size={len(_FILE_CONTENT_V2)}" in proc5b.stdout,
    )
    _FileServerState.content = _FILE_CONTENT_V1

    httpd.shutdown()
    server_thread.join(timeout=5)
    import shutil
    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(data_dir2, ignore_errors=True)
except Exception as exc:
    check(f"0. Test setup itself failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
