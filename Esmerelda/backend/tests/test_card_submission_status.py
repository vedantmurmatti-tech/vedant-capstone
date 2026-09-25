"""Tests for moodle/sync_service.py's _extract_card_submission_status()
(the new card-level submission-status extraction, added next to the
existing _extract_card_due_date()).

Context: the sync pipeline already scrapes submission_status from each
assignment's own page (_fetch_assignment_page_details(), reading its real
submissionstatustable — see test_assignment_description.py, whose fixtures
confirm the real observed phrase "Submission status\\nSubmitted for
grading"). This adds a SECOND, preferred source read straight off the
course-page card/list item — the same real text ("Submitted for grading",
"No submission", etc.) but available without the extra per-assignment page
navigation, mirroring _extract_card_due_date()'s own precedence over its
page-level fallback. The page-level fetch remains the fallback for
whichever assignment the card-level walk doesn't find a status for; nothing
about that existing fallback path changes.

Run with:
    venv/Scripts/python.exe tests/test_card_submission_status.py
"""

import subprocess
import sys
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

# Real observed card text shape: the assignment's own link/title sits in
# the same list-item wrapper as its status text (mirrors the "Due: ..."
# card layout _extract_card_due_date()'s own docstring documents from a
# real screenshot of the live site — same list-item structure, just a
# status phrase instead of a due-date badge).
_COURSE_PAGE_WITH_STATUSES = """
<html><body>
<ul class="section">
  <li class="activity assign">
    <a href="/mod/assign/view.php?id=101">Assignment 1</a>
    <div class="assign-status">Submitted for grading</div>
  </li>
  <li class="activity assign">
    <a href="/mod/assign/view.php?id=102">Assignment 2</a>
    <div class="assign-status">Not submitted</div>
  </li>
  <li class="activity assign">
    <a href="/mod/assign/view.php?id=103">Assignment 3</a>
    <div class="assign-status">No submission</div>
  </li>
  <li class="activity assign">
    <a href="/mod/assign/view.php?id=104">Assignment 4</a>
    <!-- No status text on this card at all -->
  </li>
</ul>
</body></html>
"""

_EXTRACTION_SCRIPT = """
from playwright.sync_api import sync_playwright
from moodle.sync_service import _extract_card_submission_status

html = open("FIXTURE_PATH", encoding="utf-8").read()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(html)

    for id_, name in [
        (101, "Assignment 1"),
        (102, "Assignment 2"),
        (103, "Assignment 3"),
        (104, "Assignment 4"),
    ]:
        link = page.locator(f'a[href="/mod/assign/view.php?id={id_}"]')
        status = _extract_card_submission_status(link, assignment_name=name)
        print(f"STATUS_{id_}:{status!r}")

    browser.close()
"""


def _run_extraction_script(fixture_path: Path) -> subprocess.CompletedProcess:
    script = _EXTRACTION_SCRIPT.replace("FIXTURE_PATH", str(fixture_path).replace("\\", "\\\\"))
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )


try:
    fixture_path = Path(__file__).resolve().parent / "_card_submission_status_fixture.html"
    fixture_path.write_text(_COURSE_PAGE_WITH_STATUSES, encoding="utf-8")

    proc = _run_extraction_script(fixture_path)
    check("1. Extraction subprocess exited successfully", proc.returncode == 0)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)

    check(
        "2. A submitted assignment's card ('Submitted for grading') yields a submission_status that "
        "clearly indicates it was submitted",
        "STATUS_101:'Submitted for grading'" in proc.stdout,
    )
    check(
        "3. An unsubmitted assignment's card ('Not submitted') yields a submission_status that clearly "
        "indicates it was NOT submitted",
        "STATUS_102:'Not submitted'" in proc.stdout,
    )
    check(
        "4. 'No submission' (a second real Moodle unsubmitted phrasing) is also recognized",
        "STATUS_103:'No submission'" in proc.stdout,
    )
    check(
        "5. A card with no status text at all yields None, not a crash or a fabricated value — same "
        "safe-default fallback behavior every other best-effort field in this file already uses",
        "STATUS_104:None" in proc.stdout,
    )
except Exception as exc:
    check(f"0. Card submission-status extraction test failed unexpectedly: {exc}", False)
finally:
    try:
        fixture_path.unlink(missing_ok=True)
    except Exception:
        pass


print("\n--- Summary ---")
print(f"{passed}/{passed + failed} passed")
if failed:
    sys.exit(1)
