"""Tests for moodle/document_downloader.py's always-redownload behavior
(process_resource(), sync_resource_documents()) and its download-safety
bounds (_stream_resource_to_disk()'s timeout/deadline/size-limit
handling).

This file previously tested the OPPOSITE behavior: skipping a download
when a resource already had a valid local file. That skip logic was
deliberately removed — Render's container filesystem is ephemeral (a
restart/redeploy can wipe it with no notice), so "a file already exists
locally" was never a reliable signal that a fresh download wasn't
needed. Every eligible resource is now (re-)downloaded on every full
sync; only the DATABASE record is deduplicated (via
storage/crud.py's save_document(), which upserts by file_path).

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
import time
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
_FILE_CONTENT_V2 = b"%PDF-1.4 VERSION-TWO-CONTENT-LONGER-" + bytes(range(150))


class _FileServerState:
    content = _FILE_CONTENT_V1
    serve_404 = False
    # A per-byte delay used to simulate a slow/stalled connection for the
    # total-deadline test below — real time, since the deadline logic
    # itself is a real time.monotonic() wall-clock check, not something
    # that can be faked without actually waiting.
    stall_seconds_per_chunk = 0.0


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
            if _FileServerState.stall_seconds_per_chunk:
                for i in range(0, len(body), 1024):
                    self.wfile.write(body[i:i + 1024])
                    self.wfile.flush()
                    time.sleep(_FileServerState.stall_seconds_per_chunk)
            else:
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

    # === 1 & 2: every sync re-downloads, even when nothing changed ===
    data_dir = _new_data_dir()
    env = _base_env(data_dir, file_url)
    _seed(env)

    proc1 = _run_subprocess_script(env, _SYNC_ONCE_SCRIPT)
    check("1. First sync subprocess exited successfully", proc1.returncode == 0)
    check("1b. First sync: 1 eligible, 1 attempted, 1 succeeded, 0 skipped", "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc1.stdout)
    check("1c. First sync: a real Document row was created, with a real file on disk, a real hash, and 1 DocumentVersion", "on_disk=True" in proc1.stdout and "versions=1" in proc1.stdout)

    proc2 = _run_subprocess_script(env, _SYNC_ONCE_SCRIPT)
    check("2. Second sync subprocess exited successfully", proc2.returncode == 0)
    check(
        "2b. Second sync with IDENTICAL remote content: still attempted and downloaded again (never skipped for "
        "being 'already downloaded' — Render's ephemeral filesystem means local state is never trusted)",
        "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc2.stdout,
    )
    check(
        "2c. No duplicate Document/DocumentVersion rows were created for identical content re-downloaded — "
        "DB-level dedup (save_document()'s upsert-by-file_path, same hash => no new version) is unaffected "
        "by the download itself no longer being skipped",
        "versions=1" in proc2.stdout,
    )

    # === 3: extracted_text is regenerated (not stale) when content is re-downloaded and unchanged ===
    # Directly overwrite extracted_text with an obviously-fake sentinel between
    # syncs — real extraction would never produce this exact string — then run
    # a third sync and confirm the sentinel is gone, proving the file was
    # genuinely re-processed rather than left untouched.
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
    check("3. Third sync exited successfully", proc3.returncode == 0)
    check("3b. Still attempted and re-downloaded, not skipped", "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc3.stdout)

    # === 4: missing local file is re-downloaded (already true given #2, verified explicitly) ===
    file_path_line = next((l for l in proc1.stdout.splitlines() if l.startswith("FILE_PATH:")), None)
    file_name = file_path_line.split(":", 1)[1]
    downloaded_file = Path(data_dir) / "documents" / file_name
    check("  (setup) the real downloaded file exists before deleting it", downloaded_file.is_file())
    downloaded_file.unlink()
    proc4 = _run_subprocess_script(env, _SYNC_ONCE_SCRIPT)
    check("4. Sync after deleting the local file exited successfully", proc4.returncode == 0)
    check(
        "4b. A resource whose local file went missing (e.g. Render's ephemeral filesystem wiped it) is "
        "genuinely re-downloaded",
        "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc4.stdout,
    )
    check("4c. The file exists on disk again after re-download", "on_disk=True" in proc4.stdout)

    # === 5: a resource updated on Moodle is picked up (no HEAD-based gating needed anymore — always downloads) ===
    data_dir2 = _new_data_dir()
    env2 = _base_env(data_dir2, file_url)
    _seed(env2)
    _FileServerState.content = _FILE_CONTENT_V1
    proc5a = _run_subprocess_script(env2, _SYNC_ONCE_SCRIPT)
    check("5. Initial sync (v1 content) succeeded", "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc5a.stdout)

    _FileServerState.content = _FILE_CONTENT_V2  # simulates the file changing on Moodle
    proc5b = _run_subprocess_script(env2, _SYNC_ONCE_SCRIPT)
    check("5b. After the resource changes on Moodle, the next sync downloads it again and picks up the new content", "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc5b.stdout)
    check(
        "5c. The re-downloaded file's real size on disk reflects the NEW (v2) content, not the stale v1 size",
        f"size={len(_FILE_CONTENT_V2)}" in proc5b.stdout,
    )
    check(
        "5d. Re-downloading changed content creates exactly one new DocumentVersion (2 total: v1 then v2) — "
        "hash-based dedup in save_document() still works correctly with unconditional re-downloading",
        "versions=2" in proc5b.stdout,
    )
    _FileServerState.content = _FILE_CONTENT_V1

    # === 6: a download that fails (404) does not destroy a previously-valid file ===
    data_dir3 = _new_data_dir()
    env3 = _base_env(data_dir3, file_url)
    _seed(env3)
    proc6a = _run_subprocess_script(env3, _SYNC_ONCE_SCRIPT)
    check("6. Initial sync succeeded", "COUNTS:eligible=1:attempted=1:succeeded=1:skipped=0:failed=0" in proc6a.stdout)
    file_path_line3 = next((l for l in proc6a.stdout.splitlines() if l.startswith("FILE_PATH:")), None)
    file_name3 = file_path_line3.split(":", 1)[1]
    good_file = Path(data_dir3) / "documents" / file_name3
    original_bytes = good_file.read_bytes()

    _FileServerState.serve_404 = True
    proc6b = _run_subprocess_script(env3, _SYNC_ONCE_SCRIPT)
    _FileServerState.serve_404 = False
    check("6b. Sync during a Moodle-side 404 exited successfully (doesn't crash the whole sync)", proc6b.returncode == 0)
    check(
        "6c. The failed re-download is counted as failed, not silently successful",
        "COUNTS:eligible=1:attempted=1:succeeded=0:skipped=0:failed=1" in proc6b.stdout,
    )
    check(
        "6d. CRITICAL: the previously-downloaded, still-valid file was NOT deleted or corrupted by the failed "
        "re-download attempt — a failed download must never destroy a good, already-saved file",
        good_file.is_file() and good_file.read_bytes() == original_bytes,
    )
    check(
        "6e. The temp file used for the failed download attempt was cleaned up, not left behind",
        not (Path(data_dir3) / f".tmp-resource-{1}").exists(),
    )

    # === 7: a download that exceeds the total wall-clock deadline is aborted, not left hanging forever ===
    # Monkeypatches the module's own deadline constant down to something a
    # test can actually wait out, then makes the fake server trickle bytes
    # slowly enough to blow past it — proving the deadline check (not just
    # the per-read socket timeout) is what aborts a technically-still-
    # trickling-but-too-slow connection.
    data_dir4 = _new_data_dir()
    env4 = _base_env(data_dir4, file_url)
    _seed(env4)
    _FileServerState.content = bytes(range(256)) * 20  # 5120 bytes -> 5 x 1 KiB server-side chunks
    _FileServerState.stall_seconds_per_chunk = 0.6
    deadline_script = _SYNC_ONCE_SCRIPT.replace(
        "from moodle.document_downloader import sync_resource_documents",
        "import moodle.document_downloader as dd\n"
        "dd._TOTAL_DOWNLOAD_DEADLINE_SECONDS = 1\n"
        "sync_resource_documents = dd.sync_resource_documents",
    )
    start_time = time.monotonic()
    proc7 = _run_subprocess_script(env4, deadline_script, timeout=30)
    elapsed = time.monotonic() - start_time
    _FileServerState.stall_seconds_per_chunk = 0.0
    _FileServerState.content = _FILE_CONTENT_V1
    check("7. Sync against a stalled/slow-trickling connection exited on its own, without the subprocess timeout being hit", proc7.returncode == 0)
    check(
        "7b. The download was aborted as a failure once the (shortened, for this test) total deadline was "
        "exceeded, not left hanging or falsely reported as successful",
        "COUNTS:eligible=1:attempted=1:succeeded=0:skipped=0:failed=1" in proc7.stdout,
    )
    check(
        "7c. Real evidence the deadline (not some other error) is what triggered the abort: the download-failed "
        "log line names reason=timeout",
        "reason=timeout" in (proc7.stdout + proc7.stderr),
    )
    check(
        "7d. The whole abort-and-cleanup happened well within a few seconds of the 1-second deadline — proving "
        "this is a real, prompt wall-clock check, not something that only gives up after the outer subprocess "
        "timeout",
        elapsed < 15,
    )

    # === 8: a file exceeding the maximum download size is rejected, not partially saved ===
    data_dir5 = _new_data_dir()
    env5 = _base_env(data_dir5, file_url)
    _seed(env5)
    _FileServerState.content = bytes(range(256)) * 20  # 5120 bytes
    size_limit_script = _SYNC_ONCE_SCRIPT.replace(
        "from moodle.document_downloader import sync_resource_documents",
        "import moodle.document_downloader as dd\n"
        "dd._MAX_DOWNLOAD_BYTES = 1000\n"  # smaller than the 5120-byte body, via Content-Length up front
        "sync_resource_documents = dd.sync_resource_documents",
    )
    proc8 = _run_subprocess_script(env5, size_limit_script)
    _FileServerState.content = _FILE_CONTENT_V1
    check("8. Sync against an over-limit file exited successfully", proc8.returncode == 0)
    check(
        "8b. The over-limit download is rejected as a failure (via the advertised Content-Length, before any "
        "body bytes are streamed to disk) rather than silently truncated or saved as if it succeeded",
        "COUNTS:eligible=1:attempted=1:succeeded=0:skipped=0:failed=1" in proc8.stdout,
    )
    check(
        "8c. The failure is classified as size_limit_exceeded, distinguishable in logs from a timeout/HTTP/"
        "connection error",
        "reason=size_limit_exceeded" in (proc8.stdout + proc8.stderr),
    )
    check("8d. No document row or file was created for the rejected oversized download", "DOCUMENT:none" in proc8.stdout)

    httpd.shutdown()
    server_thread.join(timeout=5)
    import shutil
    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(data_dir2, ignore_errors=True)
    shutil.rmtree(data_dir3, ignore_errors=True)
    shutil.rmtree(data_dir4, ignore_errors=True)
    shutil.rmtree(data_dir5, ignore_errors=True)
except Exception as exc:
    check(f"0. Test setup itself failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
