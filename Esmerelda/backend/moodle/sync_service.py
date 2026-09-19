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
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from storage.crud import record_login_diagnostic, save_assignment, save_course, save_resource
from moodle.document_downloader import sync_resource_documents

MOODLE_URL_DEFAULT = "https://lms.flame.edu.in"


def _safe_title(page) -> str:
    try:
        return page.title()
    except Exception:
        return "<unavailable>"

logger = logging.getLogger("esmerelda.moodle_sync")

# Bumped whenever this file's diagnostic instrumentation changes — logged
# at app startup (main.py) and returned by GET /api/sync/moodle/diagnostics
# so it's possible to confirm, from Render's own logs/API response, that
# the deployed container is actually running this exact version of this
# file and not a stale image layer. See BUILD_LOG.md.
DIAGNOSTIC_BUILD_MARKER = "moodle-diagnostic-build-2026-09-18-v2"

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
    # "_synced" fields are *persisted* counts — i.e. storage.crud's save_*()
    # call was actually made AND returned a real row, not None. Kept under
    # their original field names since api/routes.py's _run_moodle_sync()
    # already reads these to populate SyncRun.
    courses_synced: int = 0
    assignments_synced: int = 0
    resources_synced: int = 0
    # "_discovered" fields are what was actually found on the page before
    # any persistence was attempted — can be >= the "_synced" counts (e.g.
    # a course URL with no parseable Moodle id, or a resource whose course
    # row couldn't be matched, is discovered but not persisted). Tracking
    # both separately is what makes it possible to tell "Moodle returned
    # nothing" apart from "Moodle returned something but persisting it
    # failed" — deliberately not collapsed into one number.
    courses_discovered: int = 0
    assignments_discovered: int = 0
    resources_discovered: int = 0
    course_names: list[str] = field(default_factory=list)
    # Document-download counts (moodle/document_downloader.py's
    # sync_resource_documents(), called below) — separate from the fields
    # above since they're a downstream step over already-persisted
    # resources, not part of course/assignment/resource discovery itself.
    documents_eligible: int = 0
    documents_downloaded: int = 0


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


# ============================================================
# TEMPORARY PRODUCTION DIAGNOSTIC — added specifically to investigate a
# Render-only login failure that persisted after the previous _login()
# fix (which corrected a URL-only success check; see BUILD_LOG.md for
# both entries). This section captures rich, sanitized, secret-free
# snapshots of the real page state Render sees after submitting the
# login form, and keeps the most recent capture in memory so it can be
# pulled from a running Render instance via GET /api/sync/moodle/diagnostics
# (api/routes.py) without needing shell/log access.
#
# This is diagnostic-only and deliberately does NOT change how _login()
# decides success/failure — that logic is untouched in this change. It
# only *records* facts; it draws no conclusions (per instruction: don't
# assume a "login" URL means failure, and don't assume .usermenu means
# success — this code reports both as raw, separate booleans and lets a
# human read the real picture).
#
# Remove this section (and the temporary endpoint) once the Render/local
# discrepancy this was added to investigate is understood and fixed.
# ============================================================

_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
# Long opaque alphanumeric runs (20+ chars, mixing letters and digits) —
# the shape of a session id, sesskey, or CSRF token if one were ever
# rendered into visible page text. Errs toward over-redacting rather than
# risking a real token slipping through; this is a diagnostic dump, not
# user-facing text, so some false-positive redaction of ordinary long
# strings is an acceptable trade.
_OPAQUE_TOKEN_PATTERN = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{20,}\b")

_last_login_diagnostics: list[dict] = []


def get_last_login_diagnostics() -> list[dict]:
    """Temporary diagnostic accessor (see the block comment above) — the
    full list of stage-by-stage snapshots captured during the most recent
    _login() attempt in this process, oldest first. Empty if no sync has
    attempted a login yet since this process started. Every field here
    has already been sanitized; this is safe to serialize directly into
    an API response or a log line."""
    return list(_last_login_diagnostics)


def _sanitize_text(text: str, *, username: str, password: str) -> str:
    sanitized = text
    if username:
        sanitized = sanitized.replace(username, "[REDACTED_USERNAME]")
    if password:
        sanitized = sanitized.replace(password, "[REDACTED_PASSWORD]")
    sanitized = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", sanitized)
    sanitized = _OPAQUE_TOKEN_PATTERN.sub("[REDACTED_TOKEN]", sanitized)
    return sanitized


def _list_forms_and_inputs(page) -> list[dict]:
    """Names/types of visible forms and their inputs — explicitly never
    reads an input's `value` attribute, so no submitted or pre-filled
    credential value can end up here even by accident."""
    forms_info: list[dict] = []
    try:
        forms = page.locator("form")
        for i in range(forms.count()):
            form = forms.nth(i)
            try:
                fields = []
                controls = form.locator("input, button, select, textarea")
                for j in range(controls.count()):
                    el = controls.nth(j)
                    try:
                        fields.append({
                            "tag": el.evaluate("el => el.tagName.toLowerCase()"),
                            "type": el.get_attribute("type"),
                            "name": el.get_attribute("name"),
                            "id": el.get_attribute("id"),
                        })
                    except Exception:
                        continue
                forms_info.append({
                    "action": form.get_attribute("action"),
                    "method": form.get_attribute("method"),
                    "id": form.get_attribute("id"),
                    "fields": fields,
                })
            except Exception:
                continue
    except Exception:
        pass
    return forms_info


def _capture_login_diagnostics(
    page,
    stage: str,
    *,
    username: str,
    password: str,
    response_status: int | None = None,
    run_id: int | None = None,
) -> dict:
    """Records one sanitized, secret-free snapshot of the real page
    state. Reports raw facts only — deliberately makes no success/failure
    judgment (that stays entirely in _login(); this function is never
    called anywhere else).

    Persists to the database (storage.crud.record_login_diagnostic)
    whenever run_id is given, in addition to keeping the in-process
    _last_login_diagnostics list — the database write is what makes this
    visible to a GET /api/sync/moodle/diagnostics request that lands on
    a *different* process/instance than the one that ran this sync (see
    BUILD_LOG.md's diagnosis of why the in-memory-only version wasn't
    reliably visible on Render). run_id is None only for direct,
    in-process callers of _login() that don't have a SyncRun at all yet
    (e.g. tests/test_sync_service.py's regression test) — those still
    get the in-memory fallback."""
    logger.info("[SYNC DEBUG] capturing diagnostic snapshot: stage=%s run_id=%s", stage, run_id)
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
        usermenu_present = page.locator(".usermenu").count() > 0
    except Exception:
        usermenu_present = None
    try:
        logout_link_present = page.locator("a[href*='/login/logout.php']").count() > 0
    except Exception:
        logout_link_present = None
    try:
        error_el = page.locator(_LOGIN_ERROR_SELECTOR).first
        error_message = error_el.inner_text().strip()[:500] if error_el.count() > 0 else None
        if error_message:
            error_message = _sanitize_text(error_message, username=username, password=password)
    except Exception:
        error_message = None
    try:
        raw_text = page.locator("body").inner_text()
        visible_text = _sanitize_text(raw_text, username=username, password=password)[:4000]
    except Exception:
        visible_text = "<unavailable>"
    forms = _list_forms_and_inputs(page)

    snapshot = {
        "stage": stage,
        "url": url,
        "title": title,
        "login_form_present": login_form_present,
        "usermenu_present": usermenu_present,
        "logout_link_present": logout_link_present,
        "login_error_message": error_message,
        "response_status": response_status,
        "forms": forms,
        "visible_text_sanitized": visible_text,
    }
    _last_login_diagnostics.append(snapshot)
    if run_id is not None:
        try:
            record_login_diagnostic(run_id, snapshot)
        except Exception:
            logger.exception("[SYNC DEBUG] failed to persist diagnostic snapshot to the database for run_id=%s", run_id)

    logger.info(
        "[moodle-login-diagnostic] build=%s stage=%s url=%s title=%r response_status=%s "
        "login_form_present=%s usermenu_present=%s logout_link_present=%s "
        "login_error_message=%r forms=%s",
        DIAGNOSTIC_BUILD_MARKER, stage, url, title, response_status,
        login_form_present, usermenu_present, logout_link_present,
        error_message, forms,
    )
    return snapshot


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


def _login(page, moodle_url: str, username: str, password: str, run_id: int | None = None) -> None:
    """Logs in and confirms success using real, logged-in-only DOM
    elements — not the page URL. A previous version treated "the URL no
    longer contains the substring 'login'" as proof of success, which is
    unreliable: Moodle can redirect through (or land on) a URL that still
    contains "login" as a substring even after a genuinely successful
    login (e.g. a wantsurl/returnurl query parameter carried through the
    redirect chain), and the login page's own URL isn't guaranteed
    identical across visits either. Judging success by a concrete,
    logged-in-only marker element (see _LOGGED_IN_MARKER_SELECTOR) instead
    is what actually distinguishes "authenticated" from "not".

    run_id, when given, is the SyncRun row this attempt belongs to — it's
    threaded through to every diagnostic capture so those snapshots are
    persisted to the database, not just held in this process's memory."""
    _last_login_diagnostics.clear()

    try:
        page.goto(moodle_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        raise MoodleSyncError(f"Could not reach Moodle at the configured URL: {exc}") from exc

    _capture_login_diagnostics(page, "before_submit", username=username, password=password, run_id=run_id)

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
    except PlaywrightTimeoutError as exc:
        raise MoodleLoginError(f"Moodle's login form was not found or not fillable: {exc}") from exc

    submit_response_status: int | None = None
    try:
        # expect_navigation captures the real HTTP status of whatever page
        # loads as a result of the click, when Moodle does a full-page
        # navigation on login (the normal case) — None if it times out
        # (e.g. an AJAX-based login with no full navigation), which is
        # recorded as-is rather than guessed at.
        with page.expect_navigation(wait_until="domcontentloaded", timeout=20000) as nav_info:
            page.click("#loginbtn")
        response = nav_info.value
        submit_response_status = response.status if response else None
    except PlaywrightTimeoutError:
        try:
            page.click("#loginbtn")
        except Exception as exc:
            raise MoodleLoginError(f"Failed to submit Moodle's login form: {exc}") from exc
    except Exception as exc:
        raise MoodleLoginError(f"Failed to submit Moodle's login form: {exc}") from exc

    _capture_login_diagnostics(
        page, "immediately_after_submit", username=username, password=password,
        response_status=submit_response_status, run_id=run_id,
    )

    # Give Moodle's redirect chain time to settle rather than racing it —
    # wait for the network to go quiet, but don't treat a timeout here as
    # fatal by itself; the DOM checks below are still the real verdict.
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except PlaywrightTimeoutError:
        pass
    # Explicit fixed wait on top of the above, regardless of whether
    # networkidle already resolved — specifically requested so the
    # captured "after waiting" snapshot reflects a real 5-10s window, not
    # whatever networkidle happened to settle on (which can be near-
    # instant and might miss a slow server-side redirect/session write).
    page.wait_for_timeout(7000)

    _capture_login_diagnostics(page, "after_5_10s_wait", username=username, password=password, run_id=run_id)

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


def _detect_active_courses(page, moodle_url: str) -> list[dict]:
    """Generic, term-independent detection: Moodle's own dashboard groups
    courses under a "Only courses in progress" heading, ending at the next
    "Course overview" heading. This reads that same boundary (as
    moodle/browser.py already does interactively) instead of matching
    course codes/terms with a hardcoded regex (course_radar.py's approach,
    which breaks every new semester) — this survives new terms and new
    course-code prefixes without a code change.

    moodle_url anchors urljoin() below: `link.get_attribute("href")`
    returns the raw HTML attribute exactly as Moodle emitted it, which is
    not guaranteed absolute — unlike a real click or an anchor's `.href`
    DOM property (which browsers resolve automatically), Playwright's
    `page.goto()` requires an absolute URL and raises
    "Cannot navigate to invalid URL" on a bare relative path. Resolving
    every href here means every course URL stored for later navigation
    is guaranteed absolute regardless of how Moodle rendered the link."""
    logger.info("[SYNC DEBUG] active-course discovery started (page url=%s)", page.url)
    links = page.locator('a[href*="/course/view.php"]')
    link_count = links.count()
    all_courses: dict[str, str] = {}
    skipped_links = 0
    for i in range(link_count):
        try:
            link = links.nth(i)
            text = link.inner_text().strip()
            href = link.get_attribute("href")
            if href:
                href = urljoin(moodle_url, href)
            if not text or not href or "/course/view.php" not in href:
                continue
            if text not in all_courses:
                all_courses[text] = href
        except Exception as exc:
            # Logged, not silently swallowed — one bad link (e.g. detached
            # from the DOM mid-read) shouldn't kill discovery, but it also
            # shouldn't vanish without a trace.
            skipped_links += 1
            logger.warning(
                "[SYNC DEBUG] skipped one course link while reading the dashboard (link %d/%d): %s: %s",
                i, link_count, type(exc).__name__, exc,
            )
            continue
    if skipped_links:
        logger.warning("[SYNC DEBUG] active-course discovery: %d/%d course links were unreadable and skipped", skipped_links, link_count)

    try:
        body_text = page.locator("body").inner_text()
    except Exception as exc:
        logger.error("[SYNC DEBUG] could not read the Moodle dashboard body text: %s: %s", type(exc).__name__, exc)
        raise MoodleSyncError(f"Could not read the Moodle dashboard page: {exc}") from exc

    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    try:
        start = lines.index("Only courses in progress") + 1
    except ValueError:
        start = -1
        logger.warning("[SYNC DEBUG] 'Only courses in progress' heading not found on the dashboard page — active-course list will be empty")
    try:
        end = lines.index("Course overview")
    except ValueError:
        end = len(lines)
        if start != -1:
            logger.warning("[SYNC DEBUG] 'Course overview' heading not found — reading to end of page instead")

    active_names = lines[start:end] if start != -1 else []
    active_courses = [
        {"name": name, "url": all_courses[name]}
        for name in active_names
        if name in all_courses
    ]
    unmatched_names = [name for name in active_names if name not in all_courses]

    logger.info(
        "[SYNC DEBUG] active courses discovered: count=%d names=%s",
        len(active_courses), [c["name"] for c in active_courses],
    )
    if unmatched_names:
        # A name appeared in the "in progress" text section but had no
        # matching /course/view.php link anywhere on the page — genuinely
        # worth knowing about rather than silently dropping.
        logger.warning(
            "[SYNC DEBUG] %d course name(s) listed as 'in progress' had no matching course link and were dropped: %s",
            len(unmatched_names), unmatched_names,
        )
    return active_courses


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
_IGNORED_HREF_FRAGMENTS = (
    "/user/", "/course/view.php", "/mod/forum/view.php", "/grade/", "/message/", "/calendar/",
    # Present on every logged-in page's .usermenu (see _LOGGED_IN_MARKER_SELECTOR) —
    # without this, "Log out" was being discovered and persisted as a fake
    # "Activity" resource on every single course, on every sync (caught via
    # the full-pipeline regression test in tests/test_sync_service.py).
    "/login/logout.php",
)
_IGNORED_LINK_TEXT = {"more", "edit", "hide", "show", "settings"}


def _classify_resource_type(href: str) -> str:
    lower_href = href.lower()
    for fragment, label in _RESOURCE_TYPE_BY_HREF:
        if fragment in lower_href:
            return label
    return "Activity"


def _scan_page_for_resources(page, course_name: str, moodle_url: str, seen_urls: set[str]) -> tuple[int, int, int]:
    """Scans every link on whatever page is currently loaded and persists
    each real candidate as a resource. Returns (discovered, persisted,
    failed). `seen_urls` is shared across every page scanned for one
    course (the main course page plus any additional section pages — see
    _sync_course_resources()) so the same resource linked from two
    different section pages isn't double-counted.

    Deliberately does NOT filter by `link.is_visible()` — a prior version
    did, which turned out to be a real bug: Moodle commonly renders a
    course's non-current sections/topics already collapsed by default
    (a CSS `display:none`-style state, toggled open by JS on click, not
    re-fetched from the server) — confirmed directly by constructing a
    real collapsed `<div class="collapse">` in a real headless Chromium
    page and observing `link.is_visible()` return False for a link inside
    it despite the link and its href being completely real and present in
    the DOM. Since assignments are discovered separately via the
    dashboard Timeline block (never affected by a course page's own
    section collapse state), this exactly matches the reported symptom of
    assignments/deadlines succeeding while resources did not. A resource
    existing only inside a currently-collapsed section is still a real,
    persistable resource — visibility was never a meaningful filter for
    "does this resource exist," only for "is a human currently looking at
    it," so removing it can only find more real resources, never fewer."""
    discovered = 0
    persisted = 0
    failed = 0
    links = page.locator("a[href]")
    link_count = links.count()
    for i in range(link_count):
        try:
            link = links.nth(i)
            name = link.inner_text().strip()
            href = link.get_attribute("href")
            if not name or not href:
                logger.info("[SYNC DEBUG] resource link skipped on '%s' (link %d/%d): reason=no_name_or_href", course_name, i, link_count)
                continue
            if any(fragment in href for fragment in _IGNORED_HREF_FRAGMENTS):
                logger.info("[SYNC DEBUG] resource link skipped on '%s': reason=ignored_fragment href=%s", course_name, href)
                continue
            if name.lower() in _IGNORED_LINK_TEXT:
                logger.info("[SYNC DEBUG] resource link skipped on '%s': reason=ignored_text name=%r", course_name, name)
                continue
            if "/mod/assign/" in href:
                continue  # assignments are synced separately, via the timeline (has due dates) — not a skip worth logging, this is every course's normal shape
            href = urljoin(moodle_url, href)
            if href in seen_urls:
                continue  # already discovered via another link/section page to the same resource — not a skip worth logging either
            seen_urls.add(href)
            discovered += 1

            resource_type = _classify_resource_type(href)
            moodle_id = _course_moodle_id(href) or href
            saved = save_resource(
                moodle_id=moodle_id,
                course_name=course_name,
                name=name,
                resource_type=resource_type,
                url=href,
            )
            if saved is not None:
                persisted += 1
                logger.info("[SYNC DEBUG] resource written to DB: course='%s' name=%r type=%s", course_name, name, resource_type)
            else:
                logger.warning("[SYNC DEBUG] resource discovered but NOT persisted (save_resource returned None): course='%s' name=%r type=%s", course_name, name, resource_type)
        except Exception as exc:
            failed += 1
            logger.warning(
                "[SYNC DEBUG] resource link FAILED on '%s' (link %d/%d): %s: %s",
                course_name, i, link_count, type(exc).__name__, exc,
            )
            continue
    return discovered, persisted, failed


_MAX_SECTION_PAGES_PER_COURSE = 20


def _find_additional_section_pages(page, course_url: str, moodle_url: str) -> list[str]:
    """Some Moodle course-format/theme configurations ("Show one section
    per page") render each week/topic as its own page
    (`course/view.php?id=X&section=N`) rather than all sections on one
    page — in that mode, scanning only `course_url` itself would only
    ever see section 0 (announcements/general), silently missing every
    other topic's resources even with the is_visible() fix above. This
    looks for other same-course section links on the page and returns
    them (deduplicated, capped, absolute) so _sync_course_resources() can
    visit each one too. Returns an empty list on a single-page course
    (the normal case) — that course's own section-0 anchor links to
    itself and is excluded here as not "additional"."""
    course_id = _course_moodle_id(course_url)
    if not course_id:
        return []
    try:
        section_links = page.locator(f'a[href*="section="]')
        found: list[str] = []
        for i in range(section_links.count()):
            try:
                href = section_links.nth(i).get_attribute("href")
                if not href:
                    continue
                href = urljoin(moodle_url, href)
                if _course_moodle_id(href) != course_id:
                    continue  # a section link belonging to a different course — ignore
                if href == course_url or href in found:
                    continue
                found.append(href)
                if len(found) >= _MAX_SECTION_PAGES_PER_COURSE:
                    break
            except Exception:
                continue
        return found
    except Exception:
        return []


def _sync_course_resources(
    page, course_url: str, course_name: str, moodle_url: str, *, probe_multi_page_display: bool = True
) -> tuple[int, int, bool]:
    """Returns (discovered, persisted, found_multi_page_sections).

    discovered/persisted: discovered is every link that passed the
    ignore-filters below (a real candidate resource); persisted is only
    those where storage.crud.save_resource() actually returned a saved
    row (it returns None, logging its own reason, when the course row
    itself couldn't be found — see storage/crud.py).

    found_multi_page_sections: whether this course actually turned out to
    use a multi-page course display (see _find_additional_section_pages).
    run_sync() uses this to stop bothering to probe for it on later
    courses in the same sync once the first course shows it's not in use
    — see probe_multi_page_display below for why that matters.

    probe_multi_page_display: when False, skips the multi-page-display
    check entirely for this course. This exists specifically to bound a
    real risk: probing every single active course for extra section
    pages (up to _MAX_SECTION_PAGES_PER_COURSE each) meaningfully
    increases how many pages/requests one sync makes and how long it
    takes — and this project has already reproduced, concretely, a
    session being lost partway through a sync before assignment
    discovery could run (see BUILD_LOG.md). Course-display mode
    ("show all sections on one page" vs "one section per page") is
    normally a site-wide or course-format-wide setting, not something
    that varies course-by-course within the same Moodle install — so
    run_sync() checks it on the first course only, and skips the check
    entirely for the rest once it's confirmed not to apply, bounding the
    worst case to "one course pays this cost, not every course."

    moodle_url resolves each resource's raw href to an absolute URL
    before it's stored (see _detect_active_courses()'s docstring for why
    Moodle's own href attributes aren't guaranteed absolute) — a relative
    URL saved into the database would be useless to anything that later
    tries to actually open it."""
    logger.info("[SYNC DEBUG] visiting course URL: %s (%s)", course_url, course_name)
    try:
        page.goto(course_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    except Exception as exc:
        logger.error("[SYNC DEBUG] failed to open course page for '%s': %s: %s", course_name, type(exc).__name__, exc)
        raise MoodleSyncError(f"Could not open course page for '{course_name}': {exc}") from exc

    link_count = page.locator("a[href]").count()
    logger.info("[SYNC DEBUG] sections/topics discovered for '%s': %d candidate links on the main course page", course_name, link_count)

    seen_urls: set[str] = set()
    discovered, persisted, failed = _scan_page_for_resources(page, course_name, moodle_url, seen_urls)

    additional_sections = _find_additional_section_pages(page, course_url, moodle_url) if probe_multi_page_display else []
    if not probe_multi_page_display:
        logger.info("[SYNC DEBUG] '%s': skipping multi-page-display probe (already ruled out on an earlier course this sync)", course_name)
    elif additional_sections:
        logger.info(
            "[SYNC DEBUG] '%s' uses a multi-page course display — %d additional section page(s) found, visiting each",
            course_name, len(additional_sections),
        )
    for section_url in additional_sections:
        try:
            page.goto(section_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
        except Exception as exc:
            failed += 1
            logger.warning("[SYNC DEBUG] failed to open section page %s for '%s': %s: %s", section_url, course_name, type(exc).__name__, exc)
            continue
        section_discovered, section_persisted, section_failed = _scan_page_for_resources(page, course_name, moodle_url, seen_urls)
        discovered += section_discovered
        persisted += section_persisted
        failed += section_failed

    logger.info(
        "[SYNC DEBUG] resources discovered for '%s': discovered=%d persisted=%d failed=%d",
        course_name, discovered, persisted, failed,
    )
    return discovered, persisted, bool(additional_sections)


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


def _sync_course_assignments(page, active_courses: list[dict], moodle_url: str) -> tuple[int, int]:
    """Uses the dashboard Timeline block (aggregates every active
    course's upcoming/overdue assignments in one place) rather than
    visiting every course's activity list individually — matching
    submission_radar.py's approach, generalized to match against the
    real active-course list instead of a hardcoded course-code regex.

    Returns (discovered, persisted) — see _sync_course_resources()'s
    docstring for what that distinction means and why it's tracked
    separately.

    moodle_url is the real site root (the same URL passed to _login()) —
    a previous version navigated to the bare relative path "dashboard/",
    which Playwright resolves against whatever page this function is
    called from (the last course page visited by
    _sync_course_resources()), not the site root — e.g. from
    "https://.../course/view.php?id=123" that resolved to
    "https://.../course/dashboard/", a real page that does not exist on
    Moodle (whose actual dashboard is "/my/", confirmed directly from
    this project's own production login diagnostics). That silently sent
    every sync down this function's "no Timeline block found, return 0"
    path with no assignments ever persisted, even when login succeeded
    and active courses were found — this is fixed by resolving an
    explicit, absolute URL from the real site root instead."""
    dashboard_url = urljoin(moodle_url, "my/")
    logger.info("[SYNC DEBUG] navigating to dashboard for assignment discovery: %s", dashboard_url)
    try:
        page.goto(dashboard_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        logger.error(
            "[SYNC DEBUG] failed to navigate to the dashboard for assignment discovery (%s): %s: %s",
            dashboard_url, type(exc).__name__, exc,
        )
        return 0, 0
    logger.info("[SYNC DEBUG] dashboard/current page loaded: url=%s title=%r", page.url, _safe_title(page))

    # Root cause of a real regression (see BUILD_LOG.md): by the time this
    # function runs, the session may have been lost since _login() first
    # confirmed it — e.g. Moodle-side session expiry, or simply enough
    # elapsed time/request volume from resource sync (which now visits
    # more pages per course than before, see _sync_course_resources()) to
    # trip something server-side. The previous version had no way to tell
    # "genuinely no Timeline block" apart from "we got bounced back to the
    # login page" — both silently returned 0 assignments with a message
    # that explicitly (and, in the second case, wrongly) said this wasn't
    # a sign of a real problem. Checking for the login form here — the
    # same, already-proven DOM signal _login() itself uses — makes this
    # an explicit, loud MoodleSyncError instead of a silent, misleading 0.
    if _has_login_form(page):
        logger.error(
            "[SYNC DEBUG] landed on Moodle's login form while navigating to the dashboard for assignment "
            "discovery — the session was lost sometime after _login() succeeded (Moodle-side expiry, or "
            "possibly the added request volume from resource sync). This is NOT the same as 'no Timeline "
            "block found' and must not be silently reported as 0 assignments discovered."
        )
        raise MoodleSyncError(
            "Session was lost before assignment discovery could run (landed back on Moodle's login page "
            "when navigating to the dashboard). Login itself succeeded earlier in this same sync — see the "
            "diagnostics/logs for the login stage — but the session did not survive until assignment sync."
        )

    logger.info("[SYNC DEBUG] assignment discovery started (via dashboard Timeline block)")
    timeline = page.locator("section.block_timeline, .block_timeline").first
    if timeline.count() == 0:
        logger.warning(
            "[SYNC DEBUG] no Timeline block found on %s — 0 assignments discovered "
            "(this does NOT mean zero courses; it means the Timeline block wasn't present/matched on this page)",
            page.url,
        )
        return 0, 0
    try:
        timeline.wait_for(state="visible", timeout=10000)
    except Exception as exc:
        logger.warning("[SYNC DEBUG] Timeline block did not become visible within 10s: %s: %s", type(exc).__name__, exc)
    page.wait_for_timeout(1500)

    active_names = {c["name"] for c in active_courses}
    discovered = 0
    persisted = 0
    per_course_discovered: dict[str, int] = {}
    links = timeline.locator("a")
    link_count = links.count()
    logger.info("[SYNC DEBUG] Timeline block found: %d candidate link(s) to inspect", link_count)

    # Pass 1: read every candidate's data from the Timeline page itself and
    # collect it into a plain list — no navigation happens in this loop.
    # This is deliberately separate from pass 2 (below), which fetches each
    # accepted candidate's submission status and persists it. A previous
    # version did both in a single loop over the live `links` Playwright
    # locator (scoped to this Timeline page) — but fetching submission
    # status navigates the shared `page` to the assignment's own page
    # (see _fetch_submission_status()), which leaves the Timeline page
    # entirely. Every `links.nth(i)` call after that point was then
    # querying a `page` that had moved on to a completely different URL,
    # which doesn't raise cleanly — it hangs, retrying to find a match
    # that no longer exists until Playwright's own default action timeout
    # (reproduced directly: with 2+ real candidates, this cost 30+ seconds
    # per candidate after the first, moving the whole sync from ~2 seconds
    # to a full timeout/hang). This bug pre-dates this change and was
    # never triggered before only because the old "is due"-text-only
    # filter (see below) discarded every real candidate before any of
    # them ever reached the submission-status fetch at all.
    candidates: list[dict] = []
    for i in range(link_count):
        try:
            link = links.nth(i)
            # Every candidate is logged unconditionally, before any filter
            # runs — this is what let the real root cause (below) be found
            # from a real production run instead of guessed at: a prior
            # version rejected every one of these solely because
            # `re.search(r"is due", text)` didn't match the link's own
            # text, with no logging distinguishing that from any other
            # rejection reason.
            try:
                candidate_text = link.inner_text().strip()
            except Exception:
                candidate_text = "<unavailable>"
            try:
                candidate_href_raw = link.get_attribute("href")
            except Exception:
                candidate_href_raw = None
            try:
                candidate_html = link.evaluate("el => el.outerHTML")[:300]
            except Exception:
                candidate_html = "<unavailable>"
            try:
                candidate_parent_text = link.locator("..").inner_text().strip()[:200]
            except Exception:
                candidate_parent_text = "<unavailable>"
            logger.info(
                "[SYNC DEBUG] Timeline candidate %d/%d: text=%r href=%r html=%r parent_text=%r",
                i, link_count, candidate_text, candidate_href_raw, candidate_html, candidate_parent_text,
            )

            text = candidate_text
            href = candidate_href_raw
            if not href:
                logger.info("[SYNC DEBUG] Timeline candidate %d rejected: reason=no_href", i)
                continue
            href = urljoin(moodle_url, href)

            # Root cause of the real production bug this fixes (see
            # BUILD_LOG.md): identifying a Timeline entry as a real
            # assignment previously REQUIRED the literal English phrase
            # "is due" inside the link's own visible text. Moodle's actual
            # core_calendar Timeline template does not render that phrase
            # into the event-name link at all in current versions — the
            # link's text is just the activity's name (e.g. "Assessment
            # 2: ..."), with the due date/time shown as separate,
            # non-link text nearby. That silently rejected every real
            # candidate, 100% of the time, regardless of how many were
            # found. The one thing that IS still a stable, semantic,
            # Moodle-core signal (not a themeable or locale-dependent
            # string) is the href itself: every assignment activity's
            # canonical view URL is `mod/assign/view.php?id=N` — this has
            # been true since Moodle's URL routing was introduced and
            # doesn't depend on theme, version, or display language. A
            # link is now treated as a real assignment candidate if
            # EITHER its href matches that pattern OR its own text still
            # happens to say "is due" (older Moodle/theme combinations,
            # kept for backward compatibility) — never requiring the text
            # match alone, which is what silently discarded 100% of real
            # candidates in production.
            is_assignment_href = "/mod/assign/" in href
            has_is_due_text = bool(text) and bool(re.search(r"\bis due\b", text, re.I))
            if not is_assignment_href and not has_is_due_text:
                logger.info(
                    "[SYNC DEBUG] Timeline candidate %d rejected: reason=not_an_assignment_link "
                    "(href has no /mod/assign/ and text has no 'is due')",
                    i,
                )
                continue
            if not text:
                logger.info("[SYNC DEBUG] Timeline candidate %d rejected: reason=empty_text", i)
                continue

            assignment_name = re.sub(r"\s+is due\s*$", "", text, flags=re.I).strip()

            # Walk upward to find both the due date text and which active course this belongs to.
            # Whitespace is normalized before matching — real Moodle markup routinely wraps a
            # course name across nested elements, producing irregular internal whitespace/newlines
            # in inner_text() that a plain substring check against the exact discovered name (a
            # single-spaced string) would otherwise miss.
            due_date = None
            course_name = None
            parent = link
            for _ in range(8):
                try:
                    parent = parent.locator("..")
                    parent_text = parent.inner_text().strip()
                    parent_text_normalized = " ".join(parent_text.split())
                except Exception:
                    break
                if due_date is None:
                    due_date = _extract_due_date(parent_text, assignment_name)
                if course_name is None:
                    for name in active_names:
                        if " ".join(name.split()) in parent_text_normalized:
                            course_name = name
                            break
                if due_date is not None and course_name is not None:
                    break

            if course_name is None:
                logger.info(
                    "[SYNC DEBUG] Timeline candidate %d rejected: reason=no_matching_active_course "
                    "assignment=%r (checked against %d active course name(s), not one of them found in "
                    "up to 8 ancestor levels' text) — skipped, not guessed",
                    i, assignment_name, len(active_names),
                )
                continue

            discovered += 1
            per_course_discovered[course_name] = per_course_discovered.get(course_name, 0) + 1
            candidates.append({
                "moodle_id": _course_moodle_id(href) or href,
                "course_name": course_name,
                "name": assignment_name,
                "href": href,
                "due_date": due_date,
            })
        except Exception as exc:
            logger.warning(
                "[SYNC DEBUG] skipped one Timeline entry (link %d/%d): %s: %s",
                i, link_count, type(exc).__name__, exc,
            )
            continue

    # Pass 2: for each accepted candidate, fetch its submission status
    # (this navigates `page` away from the Timeline — see the note above
    # pass 1) and persist it. Safe now because nothing after this point
    # re-queries the Timeline page's own DOM — `candidates` is a plain
    # list already fully read from it.
    for candidate in candidates:
        submission_status = _fetch_submission_status(page, candidate["href"])
        saved = save_assignment(
            moodle_id=candidate["moodle_id"],
            course_name=candidate["course_name"],
            name=candidate["name"],
            submission_url=candidate["href"],
            due_date=candidate["due_date"],
            submission_status=submission_status,
        )
        if saved is not None:
            persisted += 1
            logger.info("[SYNC DEBUG] assignment written to DB: course='%s' name=%r", candidate["course_name"], candidate["name"])
        else:
            logger.warning(
                "[SYNC DEBUG] assignment discovered but NOT persisted (save_assignment returned None): course='%s' name=%r",
                candidate["course_name"], candidate["name"],
            )

    logger.info(
        "[SYNC DEBUG] assignments discovered per course: %s (total discovered=%d persisted=%d)",
        per_course_discovered, discovered, persisted,
    )
    return discovered, persisted


def run_sync(run_id: int | None = None) -> SyncResult:
    """The real sync. Raises MoodleCredentialsError/MoodleLoginError/
    MoodleSyncError on failure — never a raw Playwright/network
    exception — so the caller (api/routes.py) can record a clean error
    message on the SyncRun row without leaking a stack trace containing
    request/response internals that might include session cookies.

    run_id (the SyncRun row this call belongs to) is threaded through to
    _login() so its diagnostic captures persist to the database against
    that row — see BUILD_LOG.md for why this needed to be database-backed
    rather than only an in-process variable."""
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
                logger.info("[SYNC DEBUG] entering _login")
                _login(page, moodle_url, username, password, run_id=run_id)
                logger.info("[SYNC DEBUG] _login returned")
                logger.info("[SYNC DEBUG] dashboard/current page loaded: url=%s title=%r", page.url, _safe_title(page))

                active_courses = _detect_active_courses(page, moodle_url)
                result.courses_discovered = len(active_courses)
                # Deliberately no assumption that this is empty or non-empty —
                # both branches below are logged explicitly either way.
                if not active_courses:
                    logger.warning(
                        "[SYNC DEBUG] 0 active courses discovered — course traversal, resource "
                        "sync, and assignment sync are all skipped as a direct consequence of "
                        "this (not because they were assumed unnecessary)"
                    )

                logger.info("[SYNC DEBUG] course traversal started: %d course(s) to visit", len(active_courses))
                logger.info("[SYNC DEBUG] database persistence started")
                should_probe_multi_page = True
                for course in active_courses:
                    moodle_id = _course_moodle_id(course["url"])
                    if not moodle_id:
                        logger.warning(
                            "[SYNC DEBUG] course '%s' discovered but has no parseable Moodle id in its URL (%s) — skipped, not persisted",
                            course["name"], course["url"],
                        )
                        continue
                    save_course(moodle_id=moodle_id, name=course["name"])
                    result.course_names.append(course["name"])
                    logger.info("[SYNC DEBUG] course written to DB: name=%r moodle_id=%s", course["name"], moodle_id)

                    course_resources_discovered, course_resources_persisted, found_multi_page = _sync_course_resources(
                        page, course["url"], course["name"], moodle_url, probe_multi_page_display=should_probe_multi_page
                    )
                    result.resources_discovered += course_resources_discovered
                    result.resources_synced += course_resources_persisted
                    if should_probe_multi_page and not found_multi_page:
                        # Bounds the added-request-volume risk from the multi-page-display probe
                        # (see _sync_course_resources()'s docstring) to at most one course per sync.
                        should_probe_multi_page = False
                result.courses_synced = len(result.course_names)

                if active_courses:
                    result.assignments_discovered, result.assignments_synced = _sync_course_assignments(
                        page, active_courses, moodle_url
                    )

                # Downloads the actual file for each already-persisted, downloadable
                # Resource (PDF/DOCX/PPTX/XLSX/ZIP/etc., or a /mod/resource/ wrapper
                # page that resolves to one) and creates the matching Document/
                # DocumentVersion rows — see moodle/document_downloader.py's
                # sync_resource_documents() and BUILD_LOG.md for why this step was
                # previously entirely missing from the automated sync (Resource rows
                # were being persisted, but nothing ever downloaded their files).
                # Wrapped so a document-download problem never aborts the
                # already-successful course/assignment/resource sync above it.
                try:
                    document_counts = sync_resource_documents(page)
                    result.documents_eligible = document_counts.eligible
                    result.documents_downloaded = document_counts.succeeded
                except Exception as exc:
                    logger.exception("[SYNC DEBUG] document sync failed unexpectedly: %s: %s", type(exc).__name__, exc)

                logger.info("[SYNC DEBUG] database commit completed")
                logger.info(
                    "Moodle sync summary: courses_discovered=%d assignments_discovered=%d resources_discovered=%d "
                    "courses_persisted=%d assignments_persisted=%d resources_persisted=%d",
                    result.courses_discovered, result.assignments_discovered, result.resources_discovered,
                    result.courses_synced, result.assignments_synced, result.resources_synced,
                )
            finally:
                if context is not None:
                    context.close()
                browser.close()
    except (MoodleCredentialsError, MoodleLoginError, MoodleSyncError) as exc:
        logger.info("[SYNC DEBUG] _login raised: %s: %s", type(exc).__name__, exc)
        raise
    except Exception as exc:
        # Anything else (Playwright/browser not installed, an unexpected
        # page-structure change, a network drop mid-sync) — logged with
        # its real type and a full traceback (safe: none of the functions
        # between _login() returning and here ever receive the username/
        # password, so there is nothing credential-shaped a traceback from
        # this block could contain), then wrapped so the caller always
        # sees one of this module's own exception types.
        logger.exception("[SYNC DEBUG] sync failed after _login() with an unexpected exception: %s: %s", type(exc).__name__, exc)
        raise MoodleSyncError(f"Sync failed: {exc}") from exc

    logger.info("[SYNC DEBUG] sync completed")
    return result
