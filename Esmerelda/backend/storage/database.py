import logging
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .paths import get_database_path

# get_database_path() resolves to backend/storage/esmerelda.db by default
# (ESMERELDA_DATA_DIR unset — unchanged local behavior), or under
# ESMERELDA_DATA_DIR when it's set. See storage/paths.py.
DATABASE_URL = f"sqlite:///{get_database_path()}"

engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False
)


class Base(DeclarativeBase):
    pass


def _migrate_add_missing_columns() -> None:
    """`Base.metadata.create_all()` only creates whole tables that don't
    exist yet — it never adds a column to a table that's already there.
    `assignments.submission_status` (added for the Moodle sync pipeline's
    submission-status tracking) needs exactly that on any database created
    before this column existed, so it's added here explicitly, guarded by
    a PRAGMA check so re-running this on an already-migrated or brand-new
    database (which create_all() will have created with the column
    already present) is a safe no-op."""
    with engine.connect() as conn:
        assignments_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(assignments)"))
        }
        if assignments_columns and "submission_status" not in assignments_columns:
            conn.execute(text("ALTER TABLE assignments ADD COLUMN submission_status VARCHAR(255)"))
            conn.commit()

        # sync_runs.login_diagnostics/login_diagnostics_captured_at (temporary
        # diagnostic fields — see BUILD_LOG.md) added after sync_runs already
        # existed on some databases, so they need the same treatment.
        sync_runs_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(sync_runs)"))
        }
        if sync_runs_columns and "login_diagnostics" not in sync_runs_columns:
            conn.execute(text("ALTER TABLE sync_runs ADD COLUMN login_diagnostics TEXT"))
            conn.commit()
        if sync_runs_columns and "login_diagnostics_captured_at" not in sync_runs_columns:
            conn.execute(text("ALTER TABLE sync_runs ADD COLUMN login_diagnostics_captured_at DATETIME"))
            conn.commit()

        # documents.extracted_text — the Knowledge Base's indexed text
        # representation (see storage/models.py's Document.extracted_text
        # and storage/text_extraction.py) — added after `documents` already
        # existed on some databases.
        documents_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(documents)"))
        }
        if documents_columns and "extracted_text" not in documents_columns:
            conn.execute(text("ALTER TABLE documents ADD COLUMN extracted_text TEXT"))
            conn.commit()

        # user_id — the multi-user ownership foreign key (see
        # storage/models.py's User and BUILD_LOG.md's multi-user foundation
        # entry) added after courses/assignments/resources/documents/
        # sync_runs already existed on a real, already-populated
        # single-user database. Added nullable (SQLite can't add a NOT
        # NULL column with no default to a non-empty table in one
        # statement anyway) — backfilled to a real demo user by
        # _migrate_assign_existing_data_to_demo_user() below, not left
        # permanently null.
        for owned_table in ("courses", "assignments", "resources", "documents", "sync_runs"):
            cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({owned_table})"))}
            if cols and "user_id" not in cols:
                conn.execute(text(f"ALTER TABLE {owned_table} ADD COLUMN user_id INTEGER"))
                conn.commit()


def _get_app_meta(conn, key: str) -> str | None:
    row = conn.execute(text("SELECT value FROM app_meta WHERE key = :key"), {"key": key}).first()
    return row[0] if row else None


def _set_app_meta(conn, key: str, value: str) -> None:
    conn.execute(
        text(
            "INSERT INTO app_meta (key, value) VALUES (:key, :value) "
            "ON CONFLICT(key) DO UPDATE SET value = :value"
        ),
        {"key": key, "value": value},
    )


def _migrate_due_dates_to_utc() -> None:
    """One-time DATA migration (see storage/timezones.py's module
    docstring for the underlying bug): every Assignment.due_date value
    already in the database before this fix was stored naive-but-really-
    IST, not the naive-UTC convention every due-date consumer now
    assumes. Converts each one in place by subtracting IST's fixed 5:30
    offset, exactly once — guarded by an app_meta marker so a second
    startup (or a fresh database that was never affected in the first
    place) never double-converts an already-correct value. A freshly
    created database has no rows to convert, so this is a safe, cheap
    no-op there; the marker is still set so a later restart doesn't
    re-scan the table every time."""
    with engine.connect() as conn:
        if _get_app_meta(conn, "due_dates_converted_to_utc") is not None:
            return
        result = conn.execute(
            text("UPDATE assignments SET due_date = datetime(due_date, '-5 hours', '-30 minutes') "
                 "WHERE due_date IS NOT NULL")
        )
        _set_app_meta(conn, "due_dates_converted_to_utc", str(result.rowcount))
        conn.commit()
        if result.rowcount:
            logging.getLogger("esmerelda.sync").info(
                "[MIGRATION] converted %d existing assignment due_date value(s) from naive-IST to naive-UTC "
                "(see storage/timezones.py)", result.rowcount,
            )


_DEMO_USER_EMAIL = "demo@local.esmerelda"


def _migrate_assign_existing_data_to_demo_user() -> None:
    """One-time DATA migration, guarded the same way as
    _migrate_due_dates_to_utc() above: on a database that already has
    real single-user academic data (courses/assignments/resources/
    documents/sync_runs with user_id still NULL, from before the User
    table existed), creates one clearly-labeled local/demo User row and
    backfills every existing row's user_id to it — so nothing is deleted,
    nothing is guessed at being some arbitrary real student's data, and
    the ownership boundary introduced by this migration applies uniformly
    from this point forward. A brand-new database (nothing to backfill)
    still gets the demo user created, so local/dev flows relying on it
    (see api/auth.py) always have one to fall back to.

    Safe to run every startup — the app_meta marker makes this a no-op
    after the first real run, and re-running it manually (were the
    marker ever cleared) would still be idempotent: `WHERE user_id IS
    NULL` only ever touches rows nothing else has since claimed."""
    with engine.connect() as conn:
        if _get_app_meta(conn, "existing_data_assigned_to_demo_user") is not None:
            return

        demo_user_row = conn.execute(
            text("SELECT id FROM users WHERE email = :email"), {"email": _DEMO_USER_EMAIL}
        ).first()
        if demo_user_row is None:
            conn.execute(
                text(
                    "INSERT INTO users (email, display_name, created_at) "
                    "VALUES (:email, :name, :now)"
                ),
                {"email": _DEMO_USER_EMAIL, "name": "Local Demo User", "now": datetime.utcnow()},
            )
            demo_user_id = conn.execute(
                text("SELECT id FROM users WHERE email = :email"), {"email": _DEMO_USER_EMAIL}
            ).first()[0]
        else:
            demo_user_id = demo_user_row[0]

        counts = {}
        for owned_table in ("courses", "assignments", "resources", "documents", "sync_runs"):
            result = conn.execute(
                text(f"UPDATE {owned_table} SET user_id = :uid WHERE user_id IS NULL"),
                {"uid": demo_user_id},
            )
            counts[owned_table] = result.rowcount

        _set_app_meta(conn, "existing_data_assigned_to_demo_user", str(counts))
        conn.commit()
        if any(counts.values()):
            logging.getLogger("esmerelda.sync").info(
                "[MIGRATION] assigned pre-existing single-user data to demo user id=%d (%s) — %s",
                demo_user_id, _DEMO_USER_EMAIL, counts,
            )


def init_db():
    from . import models

    Base.metadata.create_all(bind=engine)
    _migrate_add_missing_columns()
    _migrate_due_dates_to_utc()
    _migrate_assign_existing_data_to_demo_user()


if __name__ == "__main__":
    init_db()
    print("Esmerelda database initialized successfully.")
