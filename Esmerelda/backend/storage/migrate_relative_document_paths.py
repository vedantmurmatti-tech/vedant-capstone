"""One-off migration: rewrites Document/DocumentVersion.file_path from the
absolute paths baked in before the document-path-portability fix (see
BUILD_LOG.md) to plain filenames, relative to storage/documents/ (see
storage/paths.py). Every row's real content on disk is untouched — only
the stored path string changes.

Run once, locally:
    venv/Scripts/python.exe -m storage.migrate_relative_document_paths

Safe to re-run: any row whose file_path is already a bare filename (no
directory component) is left untouched, so running it twice is a no-op
the second time.
"""

import os

from storage.database import SessionLocal
from storage.models import Document, DocumentVersion


def _to_relative(file_path: str) -> str:
    return os.path.basename(file_path)


def main() -> None:
    with SessionLocal() as session:
        documents_changed = 0
        for doc in session.query(Document).all():
            relative = _to_relative(doc.file_path)
            if relative != doc.file_path:
                print(f"Document {doc.id}: {doc.file_path!r} -> {relative!r}")
                doc.file_path = relative
                documents_changed += 1

        versions_changed = 0
        for version in session.query(DocumentVersion).all():
            relative = _to_relative(version.file_path)
            if relative != version.file_path:
                version.file_path = relative
                versions_changed += 1

        session.commit()
        print(f"\nMigrated {documents_changed} Document row(s) and {versions_changed} DocumentVersion row(s).")


if __name__ == "__main__":
    main()
