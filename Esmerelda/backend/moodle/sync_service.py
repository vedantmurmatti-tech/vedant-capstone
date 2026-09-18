"""Moodle sync service — credential-based, headless, idempotent.

Unlike moodle/browser.py, course_radar.py, content_radar.py, and
submission_radar.py (all pre-existing, all requiring a human to log in
interactively into a visible, persistent browser window), this module logs
into Moodle on its own using MOODLE_USERNAME/MOODLE_PASSWORD from the
environment, so it can run unattended from an API request.

Entry point: run_sync() — logs in, detects active courses, fetches each
active course's assignments (with due dates and, best-effort, submission
status) and resources, and upserts everything into the existing SQLite
database via storage/crud.py's already-idempotent save_*() functions (each
one updates the existing row by moodle_id if present, rather than ever
inserting a duplicate).

Called from api/routes.py's POST /api/sync/moodle, via FastAPI
BackgroundTasks, with a storage.models.SyncRun row tracking progress/
success/failure so GET /api/sync-status can report real state instead of
guessing from unrelated timestamps.

Known limitation (see BUILD_LOG.md): this needs Playwright + a real
Chromium browser installed in whatever process runs it. The production
Docker image (backend/Dockerfile) deliberately excludes Playwright, since
until now nothing in the deployed API needed it — triggering a sync from
a deployed container will fail with a clear MoodleSyncError, not a crash,
until that image is updated to include Playwright and its browser binary.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from storage.crud import save_assignment, save_course, save_resource

MOODLE_URL_DEFAULT = "https://lms.flame.edu.in"


class MoodleCredentialsError(Exception):
    """MOODLE_USERNAME/MOODLE_PASSWORD are missing from the environment."""


class MoodleLoginError(Exception):
    """Credentials were supplied but Moodle rejected them, or the login
    form couldn't be found/submitted."""


class MoodleSyncError(Exception):
    """Login succeeded but something later in the sync failed (network
    error, Moodle page structure not matching, Playwright/browser not
    installed, etc). Never wraps a credentials value into its message."""


@dataclass
class SyncResult:
    courses_synced: int = 0
    assignments_synced: int = 0
    resources_synced: int = 0
    course_names: list[str] = field(default_factory=list)


def _get_credentials() -> tuple[str, str, str]:
    username = os.environ.get("MOODLE_USERNAME")
    password = os.environ.get("MOODLE_PASSWORD")
    moodle_url = os.environ.get("MOODLE_URL") or MOODLE_URL_DEFAULT
    if not username or not password:
        raise MoodleCredentialsError(
            "MOODLE_USERNAME and MOODLE_PASSWORD must both be set in the environment "
            "for an unattended sync. (Never logged: only their presence is checked here.)"
        )
    return username, password, moodle_url


def _login(page, moodle_url: str, username: str, password: str) -> None:
    try:
        page.goto(moodle_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        raise MoodleSyncError(f"Could not reach Moodle at the configured URL: {exc}") from exc

    if "login" not in page.url.lower():
        # Already has a valid persisted session (e.g. a reused browser profile) — nothing to do.
        return

    try:
        page.fill("#username", username)
        page.fill("#password", password)
        page.click("#loginbtn")
    except PlaywrightTimeoutError as exc:
        raise MoodleLoginError(f"Moodle's login form was not found or not fillable: {exc}") from exc
    except Exception as exc:
        raise MoodleLoginError(f"Failed to submit Moodle's login form: {exc}") from exc

    try:
        page.wait_for_url(re.compile(r"^(?!.*login).*"), timeout=20000)
    except PlaywrightTimeoutError:
        # Still on the login page after submitting — either the credentials were
        # wrong, or Moodle is showing an error/CAPTCHA. Either way, this is a
        # login failure, not a network/structural one — never include the
        # password in the exception message.
        raise MoodleLoginError(
            "Moodle did not accept the configured credentials (still on the login page "
            "after submitting). Check MOODLE_USERNAME/MOODLE_PASSWORD."
        )


def _detect_active_courses(page) -> list[dict]:
    """Generic, term-independent detection: Moodle's own dashboard groups
    courses under a "Only courses in progress" heading, ending at the next
    "Course overview" heading. This reads that same boundary (as
    moodle/browser.py already does interactively) instead of matching
    course codes/terms with a hardcoded regex (course_radar.py's approach,
    which breaks every new semester) — this survives new terms and new
    course-code prefixes without a code change."""
    links = page.locator('a[href*="/course/view.php"]')
    all_courses: dict[str, str] = {}
    for i in range(links.count()):
        try:
            link = links.nth(i)
            text = link.inner_text().strip()
            href = link.get_attribute("href")
            if not text or not href or "/course/view.php" not in href:
                continue
            if text not in all_courses:
                all_courses[text] = href
        except Exception:
            continue

    try:
        body_text = page.locator("body").inner_text()
    except Exception as exc:
        raise MoodleSyncError(f"Could not read the Moodle dashboard page: {exc}") from exc

    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    try:
        start = lines.index("Only courses in progress") + 1
    except ValueError:
        start = -1
    try:
        end = lines.index("Course overview")
    except ValueError:
        end = len(lines)

    active_names = lines[start:end] if start != -1 else []
    return [
        {"name": name, "url": all_courses[name]}
        for name in active_names
        if name in all_courses
    ]


def _course_moodle_id(url: str) -> str | None:
    match = re.search(r"id=(\d+)", url)
    return match.group(1) if match else None


_RESOURCE_TYPE_BY_HREF = (
    (".pdf", "PDF"),
    (".ppt", "PowerPoint"),
    (".doc", "Word Document"),
    (".xls", "Spreadsheet"),
    ("/mod/quiz/", "Quiz"),
    ("/mod/resource/", "Resource"),
    ("/mod/page/", "Page"),
    ("/mod/url/", "Link"),
    ("/mod/folder/", "Folder"),
)
_IGNORED_HREF_FRAGMENTS = ("/user/", "/course/view.php", "/mod/forum/view.php", "/grade/", "/message/", "/calendar/")
_IGNORED_LINK_TEXT = {"more", "edit", "hide", "show", "settings"}


def _classify_resource_type(href: str) -> str:
    lower_href = href.lower()
    for fragment, label in _RESOURCE_TYPE_BY_HREF:
        if fragment in lower_href:
            return label
    return "Activity"


def _sync_course_resources(page, course_url: str, course_name: str) -> int:
    try:
        page.goto(course_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    except Exception as exc:
        raise MoodleSyncError(f"Could not open course page for '{course_name}': {exc}") from exc

    links = page.locator("a[href]")
    synced = 0
    seen_urls: set[str] = set()
    for i in range(links.count()):
        try:
            link = links.nth(i)
            if not link.is_visible():
                continue
            name = link.inner_text().strip()
            href = link.get_attribute("href")
            if not name or not href:
                continue
            if any(fragment in href for fragment in _IGNORED_HREF_FRAGMENTS):
                continue
            if name.lower() in _IGNORED_LINK_TEXT:
                continue
            if "/mod/assign/" in href:
                continue  # assignments are synced separately, via the timeline (has due dates)
            if href in seen_urls:
                continue
            seen_urls.add(href)

            moodle_id = _course_moodle_id(href) or href
            save_resource(
                moodle_id=moodle_id,
                course_name=course_name,
                name=name,
                resource_type=_classify_resource_type(href),
                url=href,
            )
            synced += 1
        except Exception:
            continue
    return synced


def _extract_due_date(text: str, assignment_name: str) -> datetime | None:
    pattern = re.compile(
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*(\d{1,2}\s+\w+\s+\d{4})\s+(\d{1,2}:\d{2})",
        re.IGNORECASE,
    )
    position = text.find(assignment_name)
    if position == -1:
        return None
    matches = list(pattern.finditer(text[:position]))
    if not matches:
        return None
    match = matches[-1]
    try:
        return datetime.strptime(f"{match.group(2)} {match.group(3)}", "%d %B %Y %H:%M")
    except ValueError:
        return None


def _fetch_submission_status(page, assignment_url: str) -> str | None:
    """Best-effort only: visits the assignment page and looks for Moodle's
    own submission-status table. Different Moodle themes/versions render
    this differently, so any failure here is swallowed — a missing
    submission status is not treated as a sync failure."""
    try:
        page.goto(assignment_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1000)
        status_table = page.locator("table.submissionstatustable, .submissionstatustable").first
        if status_table.count() == 0:
            return None
        text = status_table.inner_text()
        match = re.search(r"Submission status\s*\n?\s*(.+)", text)
        return match.group(1).strip()[:255] if match else None
    except Exception:
        return None


def _sync_course_assignments(page, active_courses: list[dict]) -> int:
    """Uses the dashboard Timeline block (aggregates every active
    course's upcoming/overdue assignments in one place) rather than
    visiting every course's activity list individually — matching
    submission_radar.py's approach, generalized to match against the
    real active-course list instead of a hardcoded course-code regex."""
    try:
        page.goto("dashboard/", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass

    timeline = page.locator("section.block_timeline, .block_timeline").first
    if timeline.count() == 0:
        return 0
    try:
        timeline.wait_for(state="visible", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    active_names = {c["name"] for c in active_courses}
    synced = 0
    links = timeline.locator("a")
    for i in range(links.count()):
        try:
            link = links.nth(i)
            if not link.is_visible():
                continue
            text = link.inner_text().strip()
            if not text or not re.search(r"\bis due\b", text, re.I):
                continue
            href = link.get_attribute("href")
            if not href:
                continue
            assignment_name = re.sub(r"\s+is due\s*$", "", text, flags=re.I).strip()

            # Walk upward to find both the due date text and which active course this belongs to.
            due_date = None
            course_name = None
            parent = link
            for _ in range(8):
                try:
                    parent = parent.locator("..")
                    parent_text = parent.inner_text().strip()
                except Exception:
                    break
                if due_date is None:
                    due_date = _extract_due_date(parent_text, assignment_name)
                if course_name is None:
                    for name in active_names:
                        if name in parent_text:
                            course_name = name
                            break
                if due_date is not None and course_name is not None:
                    break

            if course_name is None:
                continue  # not one of our currently-active courses — skip, don't guess

            moodle_id = _course_moodle_id(href) or href
            submission_status = _fetch_submission_status(page, href)
            save_assignment(
                moodle_id=moodle_id,
                course_name=course_name,
                name=assignment_name,
                submission_url=href,
                due_date=due_date,
                submission_status=submission_status,
            )
            synced += 1
        except Exception:
            continue

    return synced


def run_sync() -> SyncResult:
    """The real sync. Raises MoodleCredentialsError/MoodleLoginError/
    MoodleSyncError on failure — never a raw Playwright/network
    exception — so the caller (api/routes.py) can record a clean error
    message on the SyncRun row without leaking a stack trace containing
    request/response internals that might include session cookies."""
    username, password, moodle_url = _get_credentials()

    result = SyncResult()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                _login(page, moodle_url, username, password)

                active_courses = _detect_active_courses(page)
                for course in active_courses:
                    moodle_id = _course_moodle_id(course["url"])
                    if not moodle_id:
                        continue
                    save_course(moodle_id=moodle_id, name=course["name"])
                    result.course_names.append(course["name"])
                    result.resources_synced += _sync_course_resources(page, course["url"], course["name"])
                result.courses_synced = len(result.course_names)

                if active_courses:
                    result.assignments_synced = _sync_course_assignments(page, active_courses)
            finally:
                browser.close()
    except (MoodleCredentialsError, MoodleLoginError, MoodleSyncError):
        raise
    except Exception as exc:
        # Anything else (Playwright/browser not installed, an unexpected
        # page-structure change, a network drop mid-sync) — wrap it so the
        # caller always sees one of this module's own exception types.
        raise MoodleSyncError(f"Sync failed: {exc}") from exc

    return result
