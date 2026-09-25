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
    # .resolve() — everything downstream of this function (including
    # get_user_browser_profile_dir(), see BUILD_LOG.md's Moodle-profile-
    # path-trace entry) needs an ABSOLUTE, canonical path so two calls
    # from different request-handling contexts can never silently disagree
    # just because ESMERELDA_DATA_DIR happened to be a relative path
    # evaluated against a different process working directory. Was
    # previously left as whatever Path(override) produced verbatim — a
    # real gap, even though not confirmed to be the actual cause of any
    # specific observed bug; fixed regardless, defensively.
    data_dir = data_dir.resolve()
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


def get_mcp_readonly_snapshot_path(user_id: int | None = None) -> Path:
    """A throwaway, per-session copy of the database the SQLite MCP server
    is pointed at (see api/mcp_bridge.py) — lives alongside the real
    database so it moves with `ESMERELDA_DATA_DIR` too, but it's ephemeral:
    recreated fresh each session and deleted when the session closes.

    Keyed by user_id (multi-user isolation — see BUILD_LOG.md's
    multi-user foundation entry) both so mcp_bridge.py can filter each
    user's snapshot down to only their own rows before the read-only SQL
    tool ever opens it, AND so two users' concurrent chat requests never
    race over the same snapshot file."""
    suffix = f"_user{user_id}" if user_id is not None else ""
    return get_data_dir() / f"_esmerelda_mcp_readonly_snapshot{suffix}.db"


def get_browser_profiles_dir() -> Path:
    """Root directory for per-user Moodle Playwright profiles (see
    moodle/browser.py) — moves with ESMERELDA_DATA_DIR like everything
    else in this module. Each user gets an isolated subdirectory under
    here (get_user_browser_profile_dir()); no user's cookies/session
    state is ever stored anywhere shared."""
    profiles_dir = get_data_dir() / "browser_profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    return profiles_dir


def get_user_browser_profile_dir(user_id: int) -> Path:
    """The one Playwright `user_data_dir` for this specific Esmerelda user
    — isolated from every other user's, and from the old, pre-multi-user
    shared `moodle/browser_profile/` directory (see BUILD_LOG.md's
    multi-user foundation entry), which remains on disk untouched as the
    legacy single-account dev/demo fallback (moodle/browser.py's
    get_service_account_page()) but is never used for a real per-user
    session."""
    user_dir = get_browser_profiles_dir() / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


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
