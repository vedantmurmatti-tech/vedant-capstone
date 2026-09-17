
from pathlib import Path
from urllib.parse import unquote
import hashlib
import re
from storage.models import Resource, Document
from moodle.browser import get_authenticated_page

from storage.database import SessionLocal
from storage.models import Resource
from storage.crud import save_document


BASE_DIR = Path(__file__).resolve().parent.parent
DOCUMENTS_DIR = BASE_DIR / "storage" / "documents"

DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)


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

    save_document(
        name=destination.name,
        file_path=str(destination),
        file_hash=file_hash,
        resource_id=resource.id
    )

    print("Document metadata saved.")
    print(f"SHA-256: {file_hash}")

    return True

def main():
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
