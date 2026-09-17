from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, DateTime
from datetime import datetime

from .database import Base


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    moodle_id: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    short_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)

class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    moodle_id: Mapped[str] = mapped_column(String(255), unique=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    submission_url: Mapped[str | None] = mapped_column(String(500))

class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    moodle_id: Mapped[str] = mapped_column(String(255), unique=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))

    name: Mapped[str] = mapped_column(String(255))
    resource_type: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(Text)

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("resources.id"),
        nullable=True
    )

    name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(1000))
    file_type: Mapped[str | None] = mapped_column(String(100))
    current_hash: Mapped[str | None] = mapped_column(String(64))


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id")
    )

    file_path: Mapped[str] = mapped_column(String(1000))
    file_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )
