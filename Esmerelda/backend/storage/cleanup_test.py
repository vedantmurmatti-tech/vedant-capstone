from storage.database import SessionLocal
from storage.models import Course
from sqlalchemy import delete

with SessionLocal() as session:
    session.execute(
        delete(Course).where(Course.moodle_id == "TEST001")
    )
    session.commit()

print("Test course removed.")
