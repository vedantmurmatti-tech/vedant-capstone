from storage.crud import save_course
from storage.database import init_db

init_db()

course = save_course(
    moodle_id="TEST001",
    name="Test Course",
    short_name="TEST",
    description="Database test course"
)

print(f"Saved course: {course.name}")
print(f"Moodle ID: {course.moodle_id}")
