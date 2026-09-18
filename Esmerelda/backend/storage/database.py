from sqlalchemy import create_engine
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


def init_db():
    from . import models

    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("Esmerelda database initialized successfully.")
