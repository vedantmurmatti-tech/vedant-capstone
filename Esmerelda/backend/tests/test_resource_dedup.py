"""Tests for moodle/sync_service.py's _resource_moodle_id() — the
composite fallback identifier used when a resource's href has no
parseable Moodle activity/module id (`id=N`).

Root cause investigated: Resource.moodle_id is unique GLOBALLY, not
scoped per course. Every real Moodle activity-module view page
(`/mod/{type}/view.php?id=N` — Resource, Quiz, Page, Link, Folder) always
carries a stable `id=` (a cmid, globally unique across the whole Moodle
site by Moodle's own numbering scheme) — that's the common, reliable
case and is unchanged by this fix. The fallback only fires for a direct
file link with no such id at all: an instructor-pasted external URL, a
file embedded directly into a Label/Page's body, or an expanded Folder's
individual file listing — Moodle genuinely renders these as raw links,
and this project's own existing test fixtures already exercise exactly
this shape. Using the bare href as the fallback identifier (the previous
behavior) meant the exact same URL linked from two different courses —
a realistic scenario (a shared syllabus template, a common reading, an
external link reused across sections) — produced the identical
moodle_id, and the second course's sync would silently steal that one
Resource row away from the first course instead of creating two,
correctly separate resources.

Run with:
    venv/Scripts/python.exe tests/test_resource_dedup.py
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


def _run(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = tempfile.mkdtemp(prefix="esmerelda_resource_dedup_test_")
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# === 1: a real activity-module href (has id=) is unaffected — uses the id, not the composite form ===
_ACTIVITY_ID_SCRIPT = """
from moodle.sync_service import _resource_moodle_id
result = _resource_moodle_id("https://x/mod/resource/view.php?id=42", course_moodle_id="101", resource_type="Resource")
print(f"RESULT:{result}")
"""

# === 2: the exact same shared file URL, linked from two DIFFERENT courses, no longer collides ===
_CROSS_COURSE_COLLISION_SCRIPT = """
from moodle.sync_service import _resource_moodle_id
shared_url = "https://x/files/shared-syllabus-template.docx"
id_a = _resource_moodle_id(shared_url, course_moodle_id="101", resource_type="Word Document")
id_b = _resource_moodle_id(shared_url, course_moodle_id="202", resource_type="Word Document")
print(f"ID_A:{id_a}")
print(f"ID_B:{id_b}")
print(f"DIFFERENT:{id_a != id_b}")
"""

# === 3: full pipeline proof — the same URL persisted from two different courses creates two rows, ===
# === each correctly attached to its own course, neither stealing the other's ===
_FULL_PIPELINE_SCRIPT = """
from storage.database import init_db, SessionLocal
from storage.models import Course, Resource
from storage.crud import save_course, save_resource
from moodle.sync_service import _resource_moodle_id
init_db()

save_course(moodle_id="101", name="Course A")
save_course(moodle_id="202", name="Course B")

shared_url = "https://x/files/shared-syllabus-template.docx"
id_a = _resource_moodle_id(shared_url, course_moodle_id="101", resource_type="Word Document")
id_b = _resource_moodle_id(shared_url, course_moodle_id="202", resource_type="Word Document")

save_resource(moodle_id=id_a, course_name="Course A", name="Shared Syllabus", resource_type="Word Document", url=shared_url, course_moodle_id="101")
save_resource(moodle_id=id_b, course_name="Course B", name="Shared Syllabus", resource_type="Word Document", url=shared_url, course_moodle_id="202")

with SessionLocal() as session:
    resources = session.query(Resource).filter(Resource.url == shared_url).all()
    print(f"RESOURCE_COUNT:{len(resources)}")
    for r in resources:
        print(f"RESOURCE:{r.moodle_id}:{r.course_id}")
"""

# === 4: existing rows using the OLD (bare-href) moodle_id survive the transition — a stale row is
# === orphaned exactly once (self-correcting), never silently deleted or duplicated further on repeat syncs ===
_MIGRATION_SCRIPT = """
from storage.database import init_db, SessionLocal
from storage.models import Course, Resource
from storage.crud import save_course, save_resource
from moodle.sync_service import _resource_moodle_id
init_db()

save_course(moodle_id="101", name="Course A")

shared_url = "https://x/files/legacy-file.pdf"
with SessionLocal() as session:
    course = session.query(Course).filter(Course.moodle_id == "101").first()
    legacy_resource = Resource(moodle_id=shared_url, course_id=course.id, name="Legacy File", resource_type="PDF", url=shared_url)
    session.add(legacy_resource)
    session.commit()
    legacy_id = legacy_resource.id

new_moodle_id = _resource_moodle_id(shared_url, course_moodle_id="101", resource_type="PDF")
save_resource(moodle_id=new_moodle_id, course_name="Course A", name="Legacy File", resource_type="PDF", url=shared_url, course_moodle_id="101")
# Running the same sync again must not create a THIRD row.
save_resource(moodle_id=new_moodle_id, course_name="Course A", name="Legacy File", resource_type="PDF", url=shared_url, course_moodle_id="101")

with SessionLocal() as session:
    resources = session.query(Resource).filter(Resource.url == shared_url).all()
    print(f"TOTAL_ROWS_AFTER_TRANSITION:{len(resources)}")
    print(f"LEGACY_ROW_STILL_PRESENT:{any(r.id == legacy_id for r in resources)}")
"""


try:
    proc1 = _run(_ACTIVITY_ID_SCRIPT)
    check("1. Activity-id subprocess exited successfully", proc1.returncode == 0)
    check(
        "2. A real activity-module href (has id=) still resolves to the bare numeric id, unaffected by "
        "this fix — the common, reliable case is unchanged",
        "RESULT:42" in proc1.stdout,
    )
except Exception as exc:
    check(f"0. Activity-id test failed unexpectedly: {exc}", False)

try:
    proc2 = _run(_CROSS_COURSE_COLLISION_SCRIPT)
    check("3. Cross-course collision subprocess exited successfully", proc2.returncode == 0)
    check(
        "4. The exact same shared file URL, linked from two different courses, now produces two DIFFERENT "
        "moodle_ids — the collision that used to silently steal the resource from one course to the other "
        "is now structurally impossible",
        "DIFFERENT:True" in proc2.stdout,
    )
except Exception as exc:
    check(f"0. Cross-course collision test failed unexpectedly: {exc}", False)

try:
    proc3 = _run(_FULL_PIPELINE_SCRIPT)
    check("5. Full-pipeline subprocess exited successfully", proc3.returncode == 0)
    if proc3.returncode != 0:
        print(proc3.stdout)
        print(proc3.stderr)
    check(
        "6. CRITICAL: persisting the same shared URL from two different courses creates TWO real Resource "
        "rows in the database, not one row whose course_id got silently overwritten",
        "RESOURCE_COUNT:2" in proc3.stdout,
    )
    # rsplit, not split: a composite moodle_id itself contains colons
    # (e.g. "resource:101:Word Document:https://x/files/...") — course_id
    # is always the last field, so splitting from the right is the only
    # reliable way to isolate it.
    resource_lines = [l for l in proc3.stdout.splitlines() if l.startswith("RESOURCE:")]
    course_ids = {line.rsplit(":", 1)[1] for line in resource_lines}
    check(
        "7. The two rows are correctly attached to their own, different courses (course_id=1 and course_id=2)",
        course_ids == {"1", "2"},
    )
except Exception as exc:
    check(f"0. Full-pipeline test failed unexpectedly: {exc}", False)

try:
    proc4 = _run(_MIGRATION_SCRIPT)
    check("8. Migration-transition subprocess exited successfully", proc4.returncode == 0)
    if proc4.returncode != 0:
        print(proc4.stdout)
        print(proc4.stderr)
    check(
        "9. A pre-existing row using the OLD (bare-href) moodle_id is not deleted by the transition to the "
        "new composite identifier — it survives, and exactly one new row is added for the new identifier "
        "(a one-time, self-correcting side effect of this fix, not ongoing duplication)",
        "TOTAL_ROWS_AFTER_TRANSITION:2" in proc4.stdout and "LEGACY_ROW_STILL_PRESENT:True" in proc4.stdout,
    )
    check(
        "10. Running the same sync a second time after the transition does not create a THIRD row — the "
        "new composite identifier dedups correctly on repeat syncs, same as before",
        "TOTAL_ROWS_AFTER_TRANSITION:2" in proc4.stdout,
    )
except Exception as exc:
    check(f"0. Migration-transition test failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
