
import logging
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


DOCUMENTS_DIR = get_documents_dir()

DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("esmerelda.moodle_sync")

# Resource types (as classified by moodle/sync_service.py's own, unmodified
# _classify_resource_type() — not duplicated or changed here) that are
# real, downloadable files or file-wrapper pages, as opposed to ordinary
# course navigation/activity links, weekly section links, forums, quizzes,
# etc. "Resource" covers a generic /mod/resource/view.php wrapper page,
# which resolves to a real file only once actually inspected (see
# inspect_resource()) — everything else here is already a direct file
# link by extension.
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
    rest of that sync already has open (see process_resource()'s and
    inspect_resource()'s use of `page.context.request.get(...)` below:
    this reuses the browser context's session cookies for an authenticated
    request without ever calling page.goto() on the file itself, so a
    real file download never becomes a browser "download" event Playwright
    would otherwise need to intercept separately — it's just an
    authenticated HTTP GET returning bytes).

    Deliberately does NOT touch moodle/sync_service.py's own course/
    resource/assignment discovery, and does NOT change process_resource()
    or main() (the pre-existing interactive, get_authenticated_page()-based
    manual script) — this is purely additive, reusing every helper
    function already defined in this file."""
    counts = DocumentSyncCounts()
    resources = get_resources_eligible_for_document_sync()
    counts.eligible = len(resources)
    logger.info("[SYNC DEBUG] documents eligible for download: %d", counts.eligible)

    for resource in resources:
        with SessionLocal() as session:
            already_has_document = (
                session.query(Document).filter(Document.resource_id == resource.id).first() is not None
            )
        if already_has_document:
            counts.skipped += 1
            logger.info(
                "[SYNC DEBUG] document download skipped (already downloaded): resource_id=%d name=%r",
                resource.id, resource.name,
            )
            continue

        counts.attempted += 1
        logger.info(
            "[SYNC DEBUG] document download attempted: resource_id=%d name=%r type=%s url=%s",
            resource.id, resource.name, resource.resource_type, resource.url,
        )

        with SessionLocal() as session:
            documents_before = session.query(Document).count()
            versions_before = session.query(DocumentVersion).count()

        try:
            ok = process_resource(page, resource)
        except Exception as exc:
            ok = False
            logger.warning(
                "[SYNC DEBUG] document download FAILED: resource_id=%d name=%r: %s: %s",
                resource.id, resource.name, type(exc).__name__, exc,
            )

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
                "[SYNC DEBUG] document download succeeded: resource_id=%d name=%r "
                "(documents created=%d, versions created=%d)",
                resource.id, resource.name, new_documents, new_versions,
            )
        else:
            counts.failed += 1
            logger.warning(
                "[SYNC DEBUG] document download failed (no file found or save error): resource_id=%d name=%r",
                resource.id, resource.name,
            )

    logger.info(
        "[SYNC DEBUG] document sync summary: eligible=%d attempted=%d succeeded=%d skipped=%d failed=%d "
        "documents_created=%d versions_created=%d",
        counts.eligible, counts.attempted, counts.succeeded, counts.skipped, counts.failed,
        counts.documents_created, counts.versions_created,
    )
    return counts


def inspect_resource(page, url: str):
    try:
        response = page.context.request.get(
            url,
            timeout=30000
        )

        if not response.ok:
            print(f"HTTP error: {response.status}")
            return None

        content_type = response.headers.get(
            "content-type", ""
        ).lower()

        final_url = response.url

        # Direct file response
        if "text/html" not in content_type:
            return {
                "type": "file",
                "url": final_url,
                "content": response.body(),
                "content_type": content_type
            }

        # Moodle inline file redirect
        if "/pluginfile.php/" in final_url:
            return {
                "type": "file",
                "url": final_url,
                "content": response.body(),
                "content_type": content_type
            }

        print("Response was HTML, not a downloadable file.")
        return None

    except Exception as error:
        print(f"Could not download resource: {url}")
        print(f"Error: {error}")
        return None

def get_filename_from_url(url: str, fallback_name: str) -> str:
    filename = unquote(url.split("/")[-1].split("?")[0])

    if not filename or filename == "pluginfile.php":
        filename = fallback_name

    return safe_filename(filename)


def download_file(page, url: str, destination: Path) -> bool:
    try:
        response = page.context.request.get(
            url,
            timeout=30000
        )

        if not response.ok:
            print(f"Download failed: HTTP {response.status}")
            return False

        content_type = response.headers.get("content-type", "").lower()

        if "text/html" in content_type:
            print("Download failed: received HTML instead of a file.")
            return False

        destination.write_bytes(response.body())

        return True

    except Exception as error:
        print(f"Download failed: {url}")
        print(f"Error: {error}")
        return False


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

    result = inspect_resource(page, resource.url)

    if not result:
        print("Could not identify downloadable file.")
        return False

    filename = get_filename_from_url(
        result["url"],
        resource.name
    )

    # Use Moodle resource ID to avoid filename collisions
    safe_name = safe_filename(filename)
    destination = DOCUMENTS_DIR / safe_name

    print(f"Downloading: {safe_name}")

    try:
        destination.write_bytes(result["content"])
    except Exception as error:
        print(f"Could not save file: {error}")
        return False

    file_hash = calculate_file_hash(destination)

    # Store a filename relative to DOCUMENTS_DIR, not an absolute path —
    # portable across machines/environments. See storage/paths.py, which
    # both this downloader and the /api/documents/{id}/download route
    # resolve against, and BUILD_LOG.md for why this changed.
    save_document(
        name=destination.name,
        file_path=destination.name,
        file_hash=file_hash,
        resource_id=resource.id
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
