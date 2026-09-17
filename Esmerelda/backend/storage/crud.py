from datetime import datetime
from .models import Course, Assignment
from sqlalchemy import select
from .models import Resource
from .database import SessionLocal
from .models import Course
from .models import Document, DocumentVersion

def save_course(
    moodle_id: str,
    name: str,
    short_name: str | None = None,
    description: str | None = None
):
    with SessionLocal() as session:
        course = session.scalar(
            select(Course).where(Course.moodle_id == moodle_id)
        )

        if course is None:
            course = Course(
                moodle_id=moodle_id,
                name=name,
                short_name=short_name,
                description=description
            )
            session.add(course)
        else:
            course.name = name
            course.short_name = short_name
            course.description = description

        session.commit()
        session.refresh(course)
        return course





def save_assignment(
    moodle_id: str,
    course_name: str | None,
    name: str,
    submission_url: str | None = None,
    due_date: datetime | None = None
):
    with SessionLocal() as session:
        course = None

        if course_name:
            course = session.scalar(
                select(Course).where(Course.name == course_name)
            )

        if course is None:
            print(f"Course not found for assignment: {name}")
            return None

        assignment = session.scalar(
            select(Assignment).where(
                Assignment.moodle_id == moodle_id
            )
        )

        if assignment is None:
            assignment = Assignment(
                moodle_id=moodle_id,
                course_id=course.id,
                name=name,
                submission_url=submission_url,
                due_date=due_date
            )
            session.add(assignment)

        else:
            assignment.name = name
            assignment.course_id = course.id
            assignment.submission_url = submission_url
            assignment.due_date = due_date

        session.commit()
        session.refresh(assignment)

        return assignment




def save_resource(
    moodle_id: str,
    course_name: str,
    name: str,
    resource_type: str | None = None,
    url: str | None = None,
    description: str | None = None
):
    with SessionLocal() as session:
        course = session.scalar(
            select(Course).where(Course.name == course_name)
        )

        if course is None:
            print(f"Course not found for resource: {name}")
            return None

        resource = session.scalar(
            select(Resource).where(
                Resource.moodle_id == moodle_id
            )
        )

        if resource is None:
            resource = Resource(
                moodle_id=moodle_id,
                course_id=course.id,
                name=name,
                resource_type=resource_type,
                url=url,
                description=description
            )
            session.add(resource)

        else:
            resource.course_id = course.id
            resource.name = name
            resource.resource_type = resource_type
            resource.url = url
            resource.description = description

        session.commit()
        session.refresh(resource)

        return resource

def save_document(
    name: str,
    file_path: str,
    file_hash: str,
    resource_id: int | None = None
):
    with SessionLocal() as session:
        document = session.query(Document).filter(
            Document.file_path == file_path
        ).first()

        if document is None:
            document = Document(
                name=name,
                file_path=file_path,
                current_hash=file_hash,
                resource_id=resource_id
            )

            session.add(document)
            session.flush()

            version = DocumentVersion(
                document_id=document.id,
                file_path=file_path,
                file_hash=file_hash
            )

            session.add(version)

        elif document.current_hash != file_hash:
            document.current_hash = file_hash

            if resource_id is not None:
                document.resource_id = resource_id

            version = DocumentVersion(
                document_id=document.id,
                file_path=file_path,
                file_hash=file_hash
            )

            session.add(version)

        session.commit()
        session.refresh(document)

        return document
