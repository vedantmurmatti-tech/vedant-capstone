"""Tests for the two reported chat-agent bugs and their real root causes:

1. "Due dates display as Unknown" — traced to assignments discovered only
   via the course-page secondary discovery path
   (moodle/sync_service.py's _sync_course_page_assignments()), which had
   no Timeline text to read a due date from at all and always persisted
   due_date=NULL. Fixed by _extract_due_date_from_assignment_page() (see
   tests/test_assignment_description.py for direct extraction-level
   tests) — this file instead verifies the fix end-to-end through the
   database/API layer: a real due date, once stored, round-trips
   correctly through storage.crud.save_assignment(),
   api/queries.assignment_out(), and the real HTTP JSON response.

2. "The AI says there are no tracked assignments for a course, even
   though they exist" — traced to there being no tool that let the
   Groq/Gemini function-calling agents fetch a SPECIFIC course's
   assignment list at all: get_course_info() (api/gemini_tools.py) only
   ever returned a COUNT, never the assignments themselves, and
   get_upcoming_assignments() returns at most 8 assignments ranked by
   urgency ACROSS ALL COURSES, which can omit a specific course
   entirely. Fixed by a new get_course_assignments(course_query) tool
   that resolves the course deterministically via a real database lookup
   (api/matching.py's match_course(), enhanced in the same fix to also
   match by exact Moodle course id and short_name, and to tolerate a
   truncated/partial course name on either side) and returns its real,
   full assignment list.

Run with:
    venv/Scripts/python.exe tests/test_course_query_matching.py
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

# One shared data dir for every subprocess call in this file — the seed
# script populates it once, and every later script (match_course, the
# tool, the real HTTP API) needs to see that SAME data, not a fresh,
# empty database of its own.
_data_dir = tempfile.mkdtemp(prefix="esmerelda_course_query_test_")


def _run(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ESMERELDA_DATA_DIR"] = _data_dir
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# The real, reported scenario: a course whose stored name was truncated by
# Moodle's own dashboard rendering before this project's scraper ever saw
# it (a real, observed behavior — see BUILD_LOG.md), queried with the
# user's full, untruncated course name.
_SEED_SCRIPT = """
from datetime import datetime
from storage.database import init_db, SessionLocal
from api.auth import get_or_create_demo_user
from storage.models import Course, Assignment
from storage.crud import save_course, save_assignment
init_db()
_user_id = get_or_create_demo_user()

save_course(moodle_id="24444", name="DESG319-UGSEM5-2026/27S1-Introduction to Artificial Intelligence & Machine Lear", user_id=_user_id)
save_course(moodle_id="24443", name="DESG310-UGSEM5A-2026/27S1-Game Design", user_id=_user_id)

save_assignment(
    moodle_id="246561", course_name="DESG319-UGSEM5-2026/27S1-Introduction to Artificial Intelligence & Machine Lear",
    name="Assessment 2: Custom Skill", course_moodle_id="24444",
    due_date=datetime(2026, 9, 17, 23, 59), user_id=_user_id,
)
save_assignment(
    moodle_id="246658", course_name="DESG319-UGSEM5-2026/27S1-Introduction to Artificial Intelligence & Machine Lear",
    name="Assessment 3: MVP", course_moodle_id="24444",
    due_date=datetime(2026, 9, 18, 23, 59), user_id=_user_id,
)
# DESG310 has no assignments at all — a genuine "none tracked" case, used
# to prove the tool distinguishes this from the reported false-negative bug.
"""

_FULL_USER_QUERY = (
    "DESG319-UGSEM5-2026/27S1-Introduction to Artificial Intelligence & Machine Learning"
)


# === 1-4: match_course() resolves the same real course via every supported signal ===
_MATCH_SCRIPT = f"""
from storage.database import SessionLocal
from api.queries import fetch_courses
from api.matching import match_course
from api.auth import get_or_create_demo_user
with SessionLocal() as db:
    courses = fetch_courses(db, get_or_create_demo_user())

    by_full_name = match_course({_FULL_USER_QUERY!r}, courses)
    print(f"BY_FULL_NAME:{{by_full_name.moodle_id if by_full_name else None}}")

    by_moodle_id = match_course("24444", courses)
    print(f"BY_MOODLE_ID:{{by_moodle_id.moodle_id if by_moodle_id else None}}")

    by_code = match_course("DESG319", courses)
    print(f"BY_CODE:{{by_code.moodle_id if by_code else None}}")

    # Simulates a user pasting/typing only PART of a long course name
    # (truncated user input, cut off partway through) — the reverse of
    # the stored-name truncation check above.
    by_partial = match_course("DESG319-UGSEM5-2026/27S1-Introduction to Artificial", courses)
    print(f"BY_PARTIAL:{{by_partial.moodle_id if by_partial else None}}")

    no_match = match_course("completely unrelated text with no course reference", courses)
    print(f"NO_MATCH:{{no_match}}")
"""

# === 5-7: the new get_course_assignments tool ===
_TOOL_SCRIPT = f"""
from storage.database import SessionLocal
from api.gemini_tools import build_tools, ToolCollector
from api.auth import get_or_create_demo_user

with SessionLocal() as db:
    collector = ToolCollector()
    tools = {{t.__name__: t for t in build_tools(db, collector, get_or_create_demo_user())}}
    get_course_assignments = tools["get_course_assignments"]

    result = get_course_assignments({_FULL_USER_QUERY!r})
    print(f"RESULT_COURSE:{{result.get('course')}}")
    print(f"RESULT_ASSIGNMENT_COUNT:{{len(result.get('assignments', []))}}")
    for a in result.get("assignments", []):
        print(f"ASSIGNMENT:{{a['name']}}:{{a['due_date']}}")

    empty_result = get_course_assignments("DESG310")
    print(f"EMPTY_COURSE:{{empty_result.get('course')}}")
    print(f"EMPTY_NOTE:{{empty_result.get('note')}}")

    no_course_result = get_course_assignments("a course that does not exist at all")
    print(f"NO_COURSE_ERROR:{{'error' in no_course_result}}")

    # Structured chips (what the frontend actually renders) must be populated too.
    print(f"COLLECTOR_ASSIGNMENT_COUNT:{{len(collector.assignments)}}")
    for out in collector.assignments.values():
        print(f"COLLECTOR_DUE_DATE:{{out.dueDate}}")
"""

# === 8: due date round-trips correctly through the real HTTP API ===
_API_SCRIPT = """
from fastapi.testclient import TestClient
from main import app
client = TestClient(app)
r = client.get("/api/assignments")
for a in r.json():
    print(f"API_ASSIGNMENT:{a['name']}:{a['dueDate']}")
"""

try:
    seed_proc = _run(_SEED_SCRIPT)
    check("0. Seed subprocess exited successfully", seed_proc.returncode == 0)
    if seed_proc.returncode != 0:
        print(seed_proc.stdout)
        print(seed_proc.stderr)

    match_proc = _run(_MATCH_SCRIPT)
    check("1. match_course subprocess exited successfully", match_proc.returncode == 0)
    if match_proc.returncode != 0:
        print(match_proc.stdout)
        print(match_proc.stderr)
    check(
        "2. The user's full, untruncated course name resolves to the real course, even though the "
        "stored name is truncated (a real Moodle-side truncation this project doesn't control)",
        "BY_FULL_NAME:24444" in match_proc.stdout,
    )
    check("3. A bare Moodle course id resolves to the same real course", "BY_MOODLE_ID:24444" in match_proc.stdout)
    check("4. The course code alone still resolves correctly (regression guard, unchanged behavior)", "BY_CODE:24444" in match_proc.stdout)
    check(
        "5. Truncated USER input (only part of a long course name typed/pasted, cut off partway) still "
        "resolves correctly via the new normalized-substring matching",
        "BY_PARTIAL:24444" in match_proc.stdout,
    )
    check("6. Text with no real course reference correctly matches nothing, rather than guessing", "NO_MATCH:None" in match_proc.stdout)
except Exception as exc:
    check(f"0. match_course test failed unexpectedly: {exc}", False)

try:
    tool_proc = _run(_TOOL_SCRIPT)
    check("7. get_course_assignments tool subprocess exited successfully", tool_proc.returncode == 0)
    if tool_proc.returncode != 0:
        print(tool_proc.stdout)
        print(tool_proc.stderr)
    check(
        "8. CRITICAL: get_course_assignments resolves the real course from the user's full course-name "
        "query and returns BOTH real tracked assignments — the exact reported 'no tracked assignments' "
        "bug is fixed",
        "RESULT_ASSIGNMENT_COUNT:2" in tool_proc.stdout,
    )
    check(
        "9. CRITICAL: both returned assignments carry their real, correct due dates — not null/Unknown "
        "(IST-aware ISO strings, e.g. '+05:30' — the raw due_date passed into save_assignment() here is "
        "now interpreted as naive-UTC, per storage/timezones.py's convention, and converted to IST for "
        "display: 23:59 UTC -> 05:29 IST the next calendar day)",
        "ASSIGNMENT:Assessment 2: Custom Skill:2026-09-18T05:29:00+05:30" in tool_proc.stdout
        and "ASSIGNMENT:Assessment 3: MVP:2026-09-19T05:29:00+05:30" in tool_proc.stdout,
    )
    check(
        "10. A course that's real but genuinely has zero tracked assignments gets an honest, explicit "
        "note — distinguishable from the false-negative bug this fixes, not silently confused with it",
        "EMPTY_COURSE:DESG310-UGSEM5A-2026/27S1-Game Design" in tool_proc.stdout
        and "currently tracked" in tool_proc.stdout,
    )
    check(
        "11. A query matching no real course at all returns a real error, not a fabricated empty success",
        "NO_COURSE_ERROR:True" in tool_proc.stdout,
    )
    check(
        "12. The structured assignment data the frontend actually renders (ToolCollector.assignments, "
        "which becomes ChatResponseOut.assignments) is populated too, with real due dates — this is what "
        "makes the due date actually reach the UI's assignment cards, not just the model's own text reply",
        "COLLECTOR_ASSIGNMENT_COUNT:2" in tool_proc.stdout
        and "COLLECTOR_DUE_DATE:2026-09-18 05:29:00+05:30" in tool_proc.stdout,
    )
except Exception as exc:
    check(f"0. get_course_assignments tool test failed unexpectedly: {exc}", False)

try:
    api_proc = _run(_API_SCRIPT)
    check("13. Real HTTP API subprocess exited successfully", api_proc.returncode == 0)
    if api_proc.returncode != 0:
        print(api_proc.stdout)
        print(api_proc.stderr)
    check(
        "14. The real GET /api/assignments HTTP JSON response carries the real due date end-to-end — "
        "confirms the API layer itself never loses or nulls out a real stored due date",
        "API_ASSIGNMENT:Assessment 2: Custom Skill:2026-09-18T05:29:00+05:30" in api_proc.stdout,
    )
except Exception as exc:
    check(f"0. Real HTTP API test failed unexpectedly: {exc}", False)


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
