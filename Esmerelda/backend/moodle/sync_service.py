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

Session isolation (see BUILD_LOG.md's "repeated syncs fail after the
first" diagnosis): run_sync() launches a brand-new Chromium *browser*
process every call (`p.chromium.launch(...)`, inside a fresh
`sync_playwright()` context), and now also explicitly opens a brand-new
*browser context* on it (`browser.new_context()`) rather than relying on
`browser.new_page()`'s implicit default context. Playwright never persists
cookies/localStorage outside of an explicit `storage_state`/
`launch_persistent_context` call — neither is used here — so no state
from one run_sync() call can leak into the next one; this was confirmed,
not assumed (see the regression test in tests/test_sync_service.py). The
actual bug was in how login *success* was judged, not in session reuse —
see _login()'s docstring below.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from storage.crud import save_assignment, save_course, save_resource

MOODLE_URL_DEFAULT = "https://lms.flame.edu.in"

logger = logging.getLogger("esmerelda.moodle_sync")

# DOM signals used to judge login state — deliberately NOT the page URL.
# A prior version decided success purely by whether "login" was still a
# substring of page.url after submitting, which is fragile: some Moodle
# installs route through an intermediate URL that also happens to contain
# "login" (e.g. a wantsurl/returnurl query parameter) even after a
# genuinely successful login, and the login page's own URL can also
# change between visits (a session-carrying query string, a locale
# prefix, etc.) without that meaning anything about whether login
# succeeded. These selectors are standard across Moodle's default theme.
_LOGIN_FORM_SELECTOR = "#login #username, form#login"
_LOGIN_ERROR_SELECTOR = "#loginerrormessage, .loginerrors, .alert-danger"
_LOGGED_IN_MARKER_SELECTOR = "a[href*='/login/logout.php'], .usermenu"


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


def _log_diagnostics(page, stage: str) -> None:
    """Safe, credential-free diagnostic logging of exactly where the login
    flow stands. Never logs a form field's value, a cookie, a token, or
    any other session data — only the URL, page title, and whether
    specific, non-secret DOM elements are present."""
    try:
        url = page.url
    except Exception:
        url = "<unavailable>"
    try:
        title = page.title()
    except Exception:
        title = "<unavailable>"
    try:
        login_form_present = page.locator(_LOGIN_FORM_SELECTOR).count() > 0
    except Exception:
        login_form_present = None
    try:
        logged_in_marker_present = page.locator(_LOGGED_IN_MARKER_SELECTOR).count() > 0
    except Exception:
        logged_in_marker_present = None
    try:
        error_el = page.locator(_LOGIN_ERROR_SELECTOR).first
        error_message = error_el.inner_text().strip()[:200] if error_el.count() > 0 else None
    except Exception:
        error_message = None

    logger.info(
        "[moodle-login] stage=%s url=%s title=%r login_form_present=%s logged_in_marker_present=%s error_message=%r",
        stage, url, title, login_form_present, logged_in_marker_present, error_message,
    )


def _is_logged_in(page) -> bool:
    try:
        return page.locator(_LOGGED_IN_MARKER_SELECTOR).count() > 0
    except Exception:
        return False


def _has_login_form(page) -> bool:
    try:
        return page.locator(_LOGIN_FORM_SELECTOR).count() > 0
    except Exception:
        return False


def _login_error_text(page) -> str | None:
    try:
        error_el = page.locator(_LOGIN_ERROR_SELECTOR).first
        if error_el.count() == 0:
            return None
        return error_el.inner_text().strip()[:300] or None
    except Exception:
        return None


def _login(page, moodle_url: str, username: str, password: str) -> None:
    """Logs in and confirms success using real, logged-in-only DOM
    elements — not the page URL. A previous version treated "the URL no
    longer contains the substring 'login'" as proof of success, which is
    unreliable: Moodle can redirect through (or land on) a URL that still
    contains "login" as a substring even after a genuinely successful
    login (e.g. a wantsurl/returnurl query parameter carried through the
    redirect chain), and the login page's own URL isn't guaranteed
    identical across visits either. Judging success by a concrete,
    logged-in-only marker element (see _LOGGED_IN_MARKER_SELECTOR) instead
    is what actually distinguishes "authenticated" from "not"."""
    try:
        page.goto(moodle_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        raise MoodleSyncError(f"Could not reach Moodle at the configured URL: {exc}") from exc

    _log_diagnostics(page, "before_login")

    if not _has_login_form(page):
        if _is_logged_in(page):
            # A genuinely already-authenticated session on this fresh context/page
            # (e.g. Moodle itself redirected straight past the login form for some
            # reason) — nothing more to do.
            return
        raise MoodleSyncError(
            "Moodle showed neither a login form nor a logged-in page after navigating "
            "to the configured URL — the page structure may not match what this sync "
            "service expects. See the logged diagnostics for the real URL/title."
        )

    try:
        page.fill("#username", username)
        page.fill("#password", password)
        page.click("#loginbtn")
    except PlaywrightTimeoutError as exc:
        raise MoodleLoginError(f"Moodle's login form was not found or not fillable: {exc}") from exc
    except Exception as exc:
        raise MoodleLoginError(f"Failed to submit Moodle's login form: {exc}") from exc

    _log_diagnostics(page, "immediately_after_submit")

    # Give Moodle's redirect chain time to settle rather than racing it —
    # wait for the network to go quiet, but don't treat a timeout here as
    # fatal by itself; the DOM checks below are still the real verdict.
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    _log_diagnostics(page, "after_waiting_for_redirect")

    if _is_logged_in(page) and not _has_login_form(page):
        return

    error_message = _login_error_text(page)
    if error_message:
        raise MoodleLoginError(f"Moodle rejected the login: {error_message}")

    if _has_login_form(page):
        raise MoodleLoginError(
            "Moodle did not accept the configured credentials (the login form is still "
            "present, with no error message shown, after submitting). Check "
            "MOODLE_USERNAME/MOODLE_PASSWORD."
        )

    raise MoodleLoginError(
        "Could not confirm Moodle login succeeded (no logged-in page marker was found "
        "after submitting, and the login form is gone). See the logged diagnostics for "
        "the real page state."
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
            context = None
            try:
                # Explicit, fresh, isolated context per call — deliberately not
                # `browser.new_page()`'s implicit default context, and never a
                # `launch_persistent_context()`/reused `storage_state`. This is
                # what guarantees every sync starts with zero cookies,
                # localStorage, or session state carried over from a previous
                # run — confirmed by tests/test_sync_service.py's regression
                # test, which runs this exact sequence twice end-to-end against
                # a real local login page and asserts both succeed identically.
                context = browser.new_context()
                page = context.new_page()
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
                if context is not None:
                    context.close()
                browser.close()
    except (MoodleCredentialsError, MoodleLoginError, MoodleSyncError):
        raise
    except Exception as exc:
        # Anything else (Playwright/browser not installed, an unexpected
        # page-structure change, a network drop mid-sync) — wrap it so the
        # caller always sees one of this module's own exception types.
        raise MoodleSyncError(f"Sync failed: {exc}") from exc

    return result
