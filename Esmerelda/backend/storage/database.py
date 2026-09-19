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


def init_db():
    from . import models

    Base.metadata.create_all(bind=engine)
    _migrate_add_missing_columns()


if __name__ == "__main__":
    init_db()
    print("Esmerelda database initialized successfully.")
