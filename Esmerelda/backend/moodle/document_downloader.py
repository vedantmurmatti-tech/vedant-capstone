
import gc
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote
import hashlib
import re
from storage.models import Resource, Document, DocumentVersion
# moodle.browser (get_authenticated_page) is imported lazily, inside main()
# only — main() is the pre-existing, interactive, manual-login-only script
# entry point. Everything else in this file (in particular
# sync_resource_documents(), called from moodle/sync_service.py's
# automated run_sync()) must import cleanly without moodle/browser.py
# present at all: backend/Dockerfile's .dockerignore deliberately excludes
# moodle/browser.py (and its browser_profile/) from the production image,
# since the automated pipeline never does interactive login — a
# module-level import here would have broken every automated sync with an
# ImportError the moment this file was included in the deployed image.

from storage.database import SessionLocal
from storage.models import Resource
from storage.crud import save_document
from storage.paths import get_documents_dir
from storage.text_extraction import extract_text, guess_extractable_type


DOCUMENTS_DIR = get_documents_dir()

DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("esmerelda.moodle_sync")

# Resource types (as classified by moodle/sync_service.py's own, unmodified
# _classify_resource_type() — not duplicated or changed here) that are
# real, downloadable files or file-wrapper pages, as opposed to ordinary
# course navigation/activity links, weekly section links, forums, quizzes,
# etc. "Resource" covers a generic /mod/resource/view.php wrapper page,
# which resolves to a real file only once actually inspected (see
# _stream_resource_to_disk()) — everything else here is already a direct
# file link by extension.
_DOWNLOADABLE_RESOURCE_TYPES = {"PDF", "Word Document", "Spreadsheet", "PowerPoint", "Resource"}
# Extension-based fallback, checked directly against resource.url — catches
# real downloadable files (ZIP, and any of the same extensions above) that
# don't happen to carry one of the resource_type labels above, without
# touching moodle/sync_service.py's own classification logic at all.
_DOWNLOADABLE_URL_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".zip",
)


def calculate_file_hash(file_path: Path) -> str:
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        while chunk := file.read(8192):
            sha256.update(chunk)

    return sha256.hexdigest()


def safe_filename(name: str) -> str:
    name = unquote(name)

    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name[:180] or "moodle_document"


def get_downloadable_resources():
    with SessionLocal() as session:
        resources = session.query(Resource).all()

        return [
            resource
            for resource in resources
            if resource.url
            and "/mod/resource/view.php" in resource.url
        ]


def _is_eligible_for_document_sync(resource: Resource) -> bool:
    """Broader than get_downloadable_resources() above (which only
    matches a /mod/resource/view.php URL substring, missing the direct
    PDF/DOCX/etc. links and other mod types moodle/sync_service.py's own
    resource discovery already classifies) — used only by
    sync_resource_documents() below, the automated sync's entry point.
    Deliberately excludes ordinary navigation/activity links, weekly
    section links, forums, quizzes, pages, and folders — none of those
    are a downloadable file or a wrapper page for one."""
    if not resource.url:
        return False
    if resource.resource_type in _DOWNLOADABLE_RESOURCE_TYPES:
        return True
    lowered = resource.url.lower()
    return any(lowered.endswith(ext) or f"{ext}?" in lowered for ext in _DOWNLOADABLE_URL_EXTENSIONS)


def get_resources_eligible_for_document_sync() -> list[Resource]:
    with SessionLocal() as session:
        resources = session.query(Resource).all()
        return [r for r in resources if _is_eligible_for_document_sync(r)]


@dataclass
class DocumentSyncCounts:
    eligible: int = 0
    attempted: int = 0
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0
    documents_created: int = 0
    versions_created: int = 0


def sync_resource_documents(page) -> DocumentSyncCounts:
    """The automated entry point — called from moodle/sync_service.py's
    run_sync() using the same already-authenticated Playwright `page` the
    rest of that sync already has open. `_stream_resource_to_disk()`
    (used by process_resource(), called below) reuses that same browser
    context's real session cookies for an authenticated, streamed HTTP
    download — no new page/context is ever created here, and Moodle's
    own login is never repeated.

    Processes resources strictly one at a time, sequentially — no
    concurrency, no thread/async pool, no batching — and never preloads
    every eligible resource's *content* into memory at once (only their
    lightweight ORM rows, one query, up front — see BUILD_LOG.md for why
    even that is bounded rather than a concern in practice). This keeps
    peak memory bounded by one file's download buffer (64 KiB — see
    _stream_resource_to_disk()) at any given moment, regardless of how
    many resources exist or how large any individual file is.

    Deliberately does NOT touch moodle/sync_service.py's own course/
    resource/assignment discovery, and does NOT change main() (the
    pre-existing interactive, get_authenticated_page()-based manual
    script) — this is purely additive, reusing every helper function
    already defined in this file."""
    counts = DocumentSyncCounts()
    # Only lightweight ORM rows (id/name/url/resource_type — a handful of
    # short strings each) are held in memory at once, never file content;
    # for hundreds of resources this is at most tens of KB, nowhere near
    # the actual cause of the memory regression (see BUILD_LOG.md) —
    # noted here because instruction explicitly asked this to be
    # considered, not because this list itself needed to shrink.
    resources = get_resources_eligible_for_document_sync()
    counts.eligible = len(resources)
    logger.info("documents eligible=%d", counts.eligible)

    for index, resource in enumerate(resources, start=1):
        with SessionLocal() as session:
            already_has_document = (
                session.query(Document).filter(Document.resource_id == resource.id).first() is not None
            )
        if already_has_document:
            counts.skipped += 1
            logger.info(
                "document download %d/%d SKIPPED (already downloaded): resource_id=%d name=%r",
                index, counts.eligible, resource.id, resource.name,
            )
            continue

        counts.attempted += 1
        logger.info(
            "document download %d/%d: resource_id=%d name=%r type=%s",
            index, counts.eligible, resource.id, resource.name, resource.resource_type,
        )

        with SessionLocal() as session:
            documents_before = session.query(Document).count()
            versions_before = session.query(DocumentVersion).count()

        ok = False
        try:
            ok = process_resource(page, resource)
        except Exception as exc:
            logger.warning(
                "document download %d/%d FAILED: resource_id=%d name=%r: %s: %s",
                index, counts.eligible, resource.id, resource.name, type(exc).__name__, exc,
            )
        finally:
            # Instruction 7: after every document, release any temporary
            # references this iteration created and let the collector run.
            # `ok`/`resource` themselves are small (a bool, one ORM row) —
            # what this guards against is any large, short-lived object
            # process_resource()/_stream_resource_to_disk() created (e.g.
            # a request/response object) outliving this iteration due to a
            # reference cycle rather than simple refcounting. No file
            # content is ever held here to begin with (see
            # _stream_resource_to_disk()'s docstring) — this is a
            # deliberate, explicit safety net on top of that, not a
            # substitute for it.
            gc.collect()

        if ok:
            with SessionLocal() as session:
                documents_after = session.query(Document).count()
                versions_after = session.query(DocumentVersion).count()
            new_documents = documents_after - documents_before
            new_versions = versions_after - versions_before
            counts.documents_created += new_documents
            counts.versions_created += new_versions
            counts.succeeded += 1
            logger.info(
                "document download %d/%d succeeded: resource_id=%d name=%r "
                "(documents created=%d, versions created=%d)",
                index, counts.eligible, resource.id, resource.name, new_documents, new_versions,
            )
        else:
            counts.failed += 1
            logger.warning(
                "document download %d/%d failed (no file found or save error): resource_id=%d name=%r",
                index, counts.eligible, resource.id, resource.name,
            )

    logger.info(
        "documents_downloaded=%d documents_failed=%d "
        "[SYNC DEBUG] document sync summary: eligible=%d attempted=%d succeeded=%d skipped=%d failed=%d "
        "documents_created=%d versions_created=%d",
        counts.succeeded, counts.failed,
        counts.eligible, counts.attempted, counts.succeeded, counts.skipped, counts.failed,
        counts.documents_created, counts.versions_created,
    )
    return counts


def get_filename_from_url(url: str, fallback_name: str) -> str:
    filename = unquote(url.split("/")[-1].split("?")[0])

    if not filename or filename == "pluginfile.php":
        filename = fallback_name

    return safe_filename(filename)


_DOWNLOAD_CHUNK_SIZE = 65536  # 64 KiB — the only file-content buffer ever held in memory at once.


def _stream_resource_to_disk(page, url: str, destination: Path) -> tuple[bool, str | None]:
    """Downloads `url` straight to `destination` on disk, one 64 KiB
    chunk at a time — never holding more than one chunk of the file's
    actual content in Python memory, and never holding the whole file at
    all. Returns (ok, final_url_or_None); final_url is needed by the
    caller to derive the real filename from Moodle's redirect target
    (e.g. .../pluginfile.php/.../Lecture%20Notes.pdf), the same way the
    previous version did.

    Replaces the previous two-function design (inspect_resource() +
    download_file(), both removed — see BUILD_LOG.md) that called
    Playwright's `page.context.request.get(url).body()`, which reads the
    ENTIRE HTTP response into a single Python `bytes` object before any
    of it is even inspected, let alone written to disk. That was the
    actual, confirmed cause of this project's Render out-of-memory
    regression: with resources processed one at a time (already true
    before and unchanged here — see sync_resource_documents()), peak
    memory per download scaled with the size of that one file, and large
    files (a lecture recording, a course ZIP, etc.) could push a
    512 MB container over its limit on their own, on top of the headless
    Chromium subprocess Render's memory limit also has to cover.

    Uses Python's built-in `urllib.request` rather than Playwright's own
    request API specifically because Playwright's synchronous Python
    bindings have no way to stream a response body to disk — `.body()`
    is the only way to read it, and it always reads everything at once.
    `urllib` is standard library — no new dependency. Authentication is
    preserved by copying the already-authenticated Playwright browser
    context's real session cookies (see BUILD_LOG.md's earlier entries
    for why the automated pipeline's login happens once, in
    moodle/sync_service.py, and this function must reuse that same
    session rather than starting a new one) onto the plain HTTP request
    via a `Cookie` header — no second login, no new browser page/context.
    Content-Type is available from the response headers as soon as the
    connection is made, before reading any body bytes at all — so an
    HTML (non-file) response is detected and the connection closed
    without ever writing a byte to disk, which is also strictly less
    work than the previous version (which downloaded the *entire* HTML
    page's body just to discover it wasn't a file)."""
    try:
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in page.context.cookies(url))
    except Exception:
        cookie_header = ""

    request = urllib.request.Request(url, headers={"Cookie": cookie_header} if cookie_header else {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            final_url = response.url

            if "text/html" in content_type and "/pluginfile.php/" not in final_url:
                print("Response was HTML, not a downloadable file.")
                return False, None

            with open(destination, "wb") as f:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
            return True, final_url
    except Exception as error:
        print(f"Could not download resource: {url}")
        print(f"Error: {error}")
        # A partially-written file from an interrupted download must never be
        # mistaken for a real, complete document — remove it rather than leave
        # a truncated/corrupt file sitting under a name save_document() might
        # otherwise be told about.
        if destination.exists():
            destination.unlink(missing_ok=True)
        return False, None


def process_resource(page, resource):
    print(f"\nProcessing: {resource.name}")

    # Skip resources already stored in the database
    with SessionLocal() as session:
        existing_document = session.query(Document).filter(
            Document.resource_id == resource.id
        ).first()

        if existing_document:
            print("Already downloaded. Skipping.")
            return True

    # The real filename is only known once Moodle's redirect target (if any)
    # is seen, but the download itself streams straight to disk — so a
    # provisional, definitely-unique temporary name is used for the
    # in-progress write, then renamed to the real name once known and
    # confirmed successful. Avoids ever needing the file's bytes in memory
    # to "know" the filename before writing them out.
    temp_destination = DOCUMENTS_DIR / f".tmp-resource-{resource.id}"
    ok, final_url = _stream_resource_to_disk(page, resource.url, temp_destination)

    if not ok:
        print("Could not identify downloadable file.")
        temp_destination.unlink(missing_ok=True)
        return False

    filename = get_filename_from_url(final_url or resource.url, resource.name)
    safe_name = safe_filename(filename)
    destination = DOCUMENTS_DIR / safe_name

    print(f"Downloading: {safe_name}")

    try:
        temp_destination.replace(destination)
    except Exception as error:
        print(f"Could not save file: {error}")
        temp_destination.unlink(missing_ok=True)
        return False

    file_hash = calculate_file_hash(destination)

    # Knowledge Base indexing: extract plain text from the just-downloaded
    # file (one document at a time — see storage/text_extraction.py's own
    # docstring for why this preserves the memory discipline established
    # for downloading itself). Eligibility is derived from the real
    # downloaded file's own extension, NOT resource.resource_type —
    # a generic /mod/resource/view.php wrapper page (the overwhelming
    # majority of real Moodle PDFs/DOCX/PPTX) is classified "Resource" by
    # moodle/sync_service.py's resource-type classifier, which has nothing
    # to do with what kind of file it actually resolves to once
    # downloaded (see storage/text_extraction.py's guess_extractable_type()
    # docstring — this was a real bug, caught by testing against a real
    # downloaded file, not assumed). None for unsupported types or a
    # failed extraction — logged there, not raised; a document that can't
    # be indexed is still a real, valid download.
    extractable_type = guess_extractable_type(destination.name)
    extracted_text = extract_text(destination, extractable_type)
    logger.info(
        "document indexed: resource_id=%d name=%r extractable=%s text_chars=%d",
        resource.id, resource.name, extracted_text is not None, len(extracted_text or ""),
    )

    # Store a filename relative to DOCUMENTS_DIR, not an absolute path —
    # portable across machines/environments. See storage/paths.py, which
    # both this downloader and the /api/documents/{id}/download route
    # resolve against, and BUILD_LOG.md for why this changed.
    save_document(
        name=destination.name,
        file_path=destination.name,
        file_hash=file_hash,
        resource_id=resource.id,
        file_type=extractable_type or resource.resource_type,
        extracted_text=extracted_text,
    )

    print("Document metadata saved.")
    print(f"SHA-256: {file_hash}")

    return True

def main():
    from moodle.browser import get_authenticated_page

    print("=" * 60)
    print("ESMERELDA DOCUMENT DOWNLOADER")
    print("=" * 60)

    resources = get_downloadable_resources()

    print(f"Resources to process: {len(resources)}")

    p, context, page = get_authenticated_page()

    successful = 0
    failed = 0

    try:
        for index, resource in enumerate(resources, start=1):
            print(f"\n[{index}/{len(resources)}]")

            try:
                if process_resource(page, resource):
                    successful += 1
                else:
                    failed += 1

            except Exception as error:
                failed += 1

                print(f"Unexpected error: {resource.name}")
                print(f"Error: {error}")

    finally:
        context.close()
        p.stop()

    print("\n" + "=" * 60)
    print("DOWNLOAD SUMMARY")
    print("=" * 60)
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Storage directory: {DOCUMENTS_DIR}")


if __name__ == "__main__":
    main()
