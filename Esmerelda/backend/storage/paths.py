"""Resolves where Esmerelda's persistent data lives: the SQLite database
and the downloaded document files.

Local dev (the default — `ESMERELDA_DATA_DIR` unset): unchanged from
before. `get_data_dir()` resolves to `backend/storage/` exactly as the
individual modules used to hardcode it — `get_database_path()` to
`backend/storage/esmerelda.db`, `get_documents_dir()` to
`backend/storage/documents/`.

When `ESMERELDA_DATA_DIR` is set (e.g. a Render Persistent Disk's mount
path such as `/data`), it overrides the base directory *both* the
database and the documents resolve under, with no other code change
needed — every module that needs to know where the database or
documents live (`storage/database.py`, `api/mcp_bridge.py`,
`moodle/document_downloader.py`, `api/routes.py`) now goes through this
one module instead of separately hardcoding `storage/`. Actually setting
this env var to a real, attached disk is a Render-dashboard configuration
step, out of scope for this repository's own files — it's wired up and
ready for when that happens. See main.py's startup log for a direct,
zero-guessing way to confirm from a running instance's own logs whether
this has actually been done.
"""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    override = os.environ.get("ESMERELDA_DATA_DIR")
    data_dir = Path(override) if override else BACKEND_DIR / "storage"
    # A fresh Render Persistent Disk mount is an empty directory (the mount point
    # itself exists, but nothing under it does yet) — sqlite3 needs this
    # directory to exist before it can create esmerelda.db inside it, so
    # this is created defensively on every resolution rather than assumed.
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_database_path() -> Path:
    return get_data_dir() / "esmerelda.db"


def get_documents_dir() -> Path:
    documents_dir = get_data_dir() / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    return documents_dir


def get_mcp_readonly_snapshot_path() -> Path:
    """A throwaway, per-session copy of the database the SQLite MCP server
    is pointed at (see api/mcp_bridge.py) — lives alongside the real
    database so it moves with `ESMERELDA_DATA_DIR` too, but it's ephemeral:
    recreated fresh each session and deleted when the session closes."""
    return get_data_dir() / "_esmerelda_mcp_readonly_snapshot.db"


def resolve_document_path(file_path: str) -> Path:
    """Resolves a `Document.file_path` value to a real file location.

    Documents downloaded after this fix store a plain filename (relative
    to `get_documents_dir()`). Rows written before this fix may still
    have an absolute path baked in — those are honored as-is, so nothing
    that's already correctly resolving silently breaks if a row was
    somehow missed by the one-off migration
    (`storage/migrate_relative_document_paths.py`).
    """
    candidate = Path(file_path)
    if candidate.is_absolute():
        return candidate
    return get_documents_dir() / candidate
