"""Tests for the deterministic, non-Playwright-dependent logic in
moodle/sync_service.py: credential validation, resource-type
classification, Moodle course-id extraction, and due-date parsing.

Not a pytest suite (pytest isn't installed) — plain assert-style checks,
matching the existing convention (see tests/test_followups.py).

Deliberately does NOT drive a real or mocked Playwright browser — doing
so meaningfully (a real login form, a real dashboard DOM, a real
Timeline block) needs a live Moodle session, which this environment does
not have. See BUILD_LOG.md for this and the sync pipeline's other
documented, honest limitations.

Run with:
    venv/Scripts/python.exe tests/test_sync_service.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moodle.sync_service import (
    MoodleCredentialsError,
    _classify_resource_type,
    _course_moodle_id,
    _extract_due_date,
    _get_credentials,
)

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


# 1. Missing credentials raise a clean, specific error — not a crash.
os.environ.pop("MOODLE_USERNAME", None)
os.environ.pop("MOODLE_PASSWORD", None)
try:
    _get_credentials()
    check("1. Missing credentials raise MoodleCredentialsError", False)
except MoodleCredentialsError as exc:
    check(
        "1. Missing credentials raise MoodleCredentialsError, message names no secret",
        "MOODLE_USERNAME" in str(exc) and "MOODLE_PASSWORD" in str(exc),
    )

# 1b. Present credentials are read back correctly, including a custom MOODLE_URL.
os.environ["MOODLE_USERNAME"] = "student123"
os.environ["MOODLE_PASSWORD"] = "not-a-real-password"
os.environ["MOODLE_URL"] = "https://lms.example.edu"
username, password, moodle_url = _get_credentials()
check(
    "1b. Present credentials + custom MOODLE_URL are read back exactly",
    (username, password, moodle_url) == ("student123", "not-a-real-password", "https://lms.example.edu"),
)
os.environ.pop("MOODLE_USERNAME", None)
os.environ.pop("MOODLE_PASSWORD", None)
os.environ.pop("MOODLE_URL", None)

# 1c. Default MOODLE_URL is used when unset.
os.environ["MOODLE_USERNAME"] = "u"
os.environ["MOODLE_PASSWORD"] = "p"
_, _, default_url = _get_credentials()
check("1c. Default MOODLE_URL used when unset", default_url == "https://lms.flame.edu.in")
os.environ.pop("MOODLE_USERNAME", None)
os.environ.pop("MOODLE_PASSWORD", None)

# 2. Resource-type classification matches real Moodle URL patterns.
check("2. PDF file classified", _classify_resource_type("https://x/files.pdf") == "PDF")
check("2b. mod/resource/ classified", _classify_resource_type("https://x/mod/resource/view.php?id=1") == "Resource")
check("2c. mod/quiz/ classified", _classify_resource_type("https://x/mod/quiz/view.php?id=1") == "Quiz")
check("2d. Unrecognized href falls back to Activity", _classify_resource_type("https://x/mod/lti/view.php?id=1") == "Activity")

# 3. Moodle numeric id extraction from a real course/assignment URL shape.
check("3. Extracts numeric id from a course URL", _course_moodle_id("https://x/course/view.php?id=24527") == "24527")
check("3b. Returns None when there's no id= in the URL", _course_moodle_id("https://x/course/view.php") is None)

# 4. Due-date parsing from real Moodle Timeline-block text shape.
timeline_text = "Header noise\nFriday, 20 September 2026 11:59\nAssessment 2 is due"
due = _extract_due_date(timeline_text, "Assessment 2 is due")
check("4. Due date parsed from timeline text", due is not None and (due.year, due.month, due.day, due.hour, due.minute) == (2026, 9, 20, 11, 59))
check("4b. No match when the assignment name isn't present", _extract_due_date(timeline_text, "Some other assignment is due") is None)
check("4c. No crash, returns None, when there's no date in the preceding text", _extract_due_date("Assessment 2 is due", "Assessment 2 is due") is None)

print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
