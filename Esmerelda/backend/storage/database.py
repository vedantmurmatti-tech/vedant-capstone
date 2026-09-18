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
        existing_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(assignments)"))
        }
        if existing_columns and "submission_status" not in existing_columns:
            conn.execute(text("ALTER TABLE assignments ADD COLUMN submission_status VARCHAR(255)"))
            conn.commit()


def init_db():
    from . import models

    Base.metadata.create_all(bind=engine)
    _migrate_add_missing_columns()


if __name__ == "__main__":
    init_db()
    print("Esmerelda database initialized successfully.")
