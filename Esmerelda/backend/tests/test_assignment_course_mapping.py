"""Tests for storage/crud.py's course-mapping fix in save_assignment()/
save_resource()/save_course() — the root cause of production's "Course
not found for assignment" errors.

Root cause (see BUILD_LOG.md): save_assignment() previously matched a
course EXCLUSIVELY by an exact `Course.name == course_name` string
comparison, using a course name scraped from fragile Timeline DOM text
matching. Both Course and Assignment already have a stable, unique
`moodle_id` column that was never used for this lookup. Any mismatch —
whitespace, HTML entity decoding, a course renamed since the last sync,
markup differences between how the dashboard renders a course name in
different contexts — silently failed the lookup and discarded the
assignment, hence "Course not found for assignment" in production logs
even for real, active courses.

The fix: prefer a stable `course_moodle_id` when available, falling back
to name matching only when no id is available or the id lookup misses.
This file also covers the related "existing metadata silently
overwritten with None" bug fixed in the same functions.

Run with:
    venv/Scripts/python.exe tests/test_assignment_course_mapping.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        print(f"[PASS] {label}")
        passed += 1
    else:
        print(f"[FAIL] {label}")
        failed += 1


backend_dir = Path(__file__).resolve().parent.parent

_SCRIPT = """
from datetime import datetime
from storage.database import init_db, SessionLocal
from storage.models import Assignment, Course, Resource
from storage.crud import save_assignment, save_course, save_resource

init_db()

# === 1: stable moodle_id mapping succeeds even when course_name doesn't match exactly ===
save_course(moodle_id="101", name="Intro to Testing")
result = save_assignment(
    moodle_id="assign-1",
    course_name="Intro  to Testing (mismatched whitespace)",
    name="Assignment 1",
    course_moodle_id="101",
)
print(f"CHECK1:{result is not None and result.course_id == 1}")

# === 2: falls back to name matching when no course_moodle_id is available ===
save_course(moodle_id="102", name="Fallback Course")
result2 = save_assignment(moodle_id="assign-2", course_name="Fallback Course", name="Assignment 2")
print(f"CHECK2:{result2 is not None}")

# === 3: a genuinely unmappable assignment returns None, not a crash ===
result3 = save_assignment(
    moodle_id="assign-3", course_name="Nonexistent Course", name="Assignment 3", course_moodle_id="999"
)
print(f"CHECK3:{result3 is None}")

# === 4: an assignment moodle_id is a stable dedup key ===
save_assignment(
    moodle_id="assign-1", course_name="Intro to Testing", name="Assignment 1 (renamed)", course_moodle_id="101"
)
with SessionLocal() as session:
    matches = session.query(Assignment).filter(Assignment.moodle_id == "assign-1").all()
print(f"CHECK4:{len(matches) == 1 and matches[0].name == 'Assignment 1 (renamed)'}")

# === 5: existing due_date/submission_url survive an update pass with no new value ===
save_assignment(
    moodle_id="assign-5",
    course_name="Intro to Testing",
    name="Assignment 5",
    course_moodle_id="101",
    due_date=datetime(2026, 1, 1, 12, 0),
    submission_url="https://example.com/assign5",
)
save_assignment(
    moodle_id="assign-5",
    course_name="Intro to Testing",
    name="Assignment 5",
    course_moodle_id="101",
    due_date=None,
    submission_url=None,
)
with SessionLocal() as session:
    a5 = session.query(Assignment).filter(Assignment.moodle_id == "assign-5").first()
print(f"CHECK5:{a5.due_date == datetime(2026, 1, 1, 12, 0) and a5.submission_url == 'https://example.com/assign5'}")

# === 6: save_resource() has the same stable-id-preferred mapping ===
resource_result = save_resource(
    moodle_id="res-1",
    course_name="Intro  to Testing (mismatched)",
    name="Lecture Slides",
    course_moodle_id="101",
)
print(f"CHECK6:{resource_result is not None and resource_result.course_id == 1}")

# === 7: save_course() does not wipe existing short_name/description with None later ===
save_course(moodle_id="103", name="Course With Metadata", short_name="CWM101", description="A real description")
save_course(moodle_id="103", name="Course With Metadata")
with SessionLocal() as session:
    c = session.query(Course).filter(Course.moodle_id == "103").first()
print(f"CHECK7:{c.short_name == 'CWM101' and c.description == 'A real description'}")

# === 8: save_assignment() persists any validly-mapped course; active-course filtering is the caller's job ===
result8 = save_assignment(moodle_id="assign-8", course_name="Fallback Course", name="Assignment 8")
print(f"CHECK8:{result8 is not None}")
"""


def _run() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tempfile.mkdtemp(prefix="esmerelda_course_mapping_test_")
    return subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


try:
    proc = _run()
    check("0. Subprocess exited successfully", proc.returncode == 0)
    if proc.returncode != 0:
        print("--- stdout ---")
        print(proc.stdout)
        print("--- stderr ---")
        print(proc.stderr)

    lines = dict(l.split(":", 1) for l in proc.stdout.splitlines() if l.startswith("CHECK"))

    check(
        "1. An assignment is correctly mapped to its course via a stable course_moodle_id, even though the "
        "scraped course_name text does not exactly match Course.name in the database",
        lines.get("CHECK1") == "True",
    )
    check(
        "2. With no course_moodle_id available at all, save_assignment() still succeeds via the course_name "
        "fallback (existing behavior preserved, not removed)",
        lines.get("CHECK2") == "True",
    )
    check("3. An assignment for a course that truly doesn't exist returns None rather than raising", lines.get("CHECK3") == "True")
    check(
        "4. Saving the same assignment moodle_id twice updates the existing row instead of creating a duplicate",
        lines.get("CHECK4") == "True",
    )
    check(
        "5. A later sync pass that couldn't determine a due_date/submission_url does not erase a previously "
        "known real value by overwriting it with None",
        lines.get("CHECK5") == "True",
    )
    check(
        "6. save_resource() also maps to its course via a stable course_moodle_id despite a non-exact course_name",
        lines.get("CHECK6") == "True",
    )
    check(
        "7. A later save_course() call without short_name/description does not erase previously-saved real values",
        lines.get("CHECK7") == "True",
    )
    check(
        "8. save_assignment() persists any course it's given a valid mapping for — active-course restriction is "
        "enforced by the caller (moodle/sync_service.py only ever calls this for candidates matched against the "
        "active-course list), confirmed by design here",
        lines.get("CHECK8") == "True",
    )
except Exception as exc:
    check(f"0. Test setup itself failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
