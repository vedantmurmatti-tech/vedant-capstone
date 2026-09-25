import logging
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from storage.paths import get_user_browser_profile_dir

MOODLE_URL = "https://lms.flame.edu.in"

# TEMPORARY, opt-in, OFF by default — a controlled test, not a behavior
# change (see BUILD_LOG.md's session-lifecycle entry). check_moodle_session()
# normally launches headless=True regardless of this; setting this env var
# makes it launch headed instead, ONLY so a real connect -> immediate
# headed check can be compared against a real connect -> immediate
# headless check, to test whether the browser's OWN fingerprint (a real,
# directly observed difference — see below) affects whether Google/Moodle
# still considers the session valid. Requires a real display (same
# constraint connect_user_interactively() already has); never touches
# credentials either way.
_FORCE_HEADED_SESSION_CHECK = os.environ.get("ESMERELDA_MOODLE_CHECK_HEADED", "0") == "1"
# Legacy, pre-multi-user shared profile — see BUILD_LOG.md's multi-user
# foundation entry. Left on disk and still used by this module's own
# main()/get_authenticated_page() (the standalone interactive tool this
# file has always been — never wired into api/routes.py's live sync
# pipeline, which is moodle/sync_service.py's separate, credential-based
# run_sync()), but every NEW per-user flow below uses
# get_user_browser_profile_dir(user_id) instead, never this constant.
PROFILE_DIR = Path(__file__).parent / "browser_profile"

logger = logging.getLogger("esmerelda.sync")

_LOGGED_IN_MARKER_SELECTOR = "a[href*='/login/logout.php'], .usermenu"
_LOGIN_FORM_SELECTOR = "#login #username, form#login"


def _cookie_names_only(context) -> list[str]:
    """Cookie NAMES only, never values — see this module's own
    diagnostics' explicit "never log cookie values, auth tokens, Google
    credentials, session IDs" requirement. Deduplicated and sorted so two
    log lines are directly, visually comparable."""
    try:
        return sorted({c.get("name", "") for c in context.cookies() if c.get("name")})
    except Exception:
        return []


def _local_storage_keys_only(page) -> list[str]:
    """localStorage KEY names only, never values. Best-effort — some
    pages/origins can throw on localStorage access (e.g. a third-party
    iframe context); never treated as an error."""
    try:
        return page.evaluate("Object.keys(window.localStorage || {})")
    except Exception:
        return []


def check_moodle_session(user_id: int, *, headless: bool = True) -> str:
    """Loads this user's own persisted Playwright profile (see
    get_user_browser_profile_dir()), navigates to Moodle, and reports
    whether the persisted session is still authenticated — WITHOUT ever
    attempting a login itself (no credentials touched, no form filled).
    Returns "connected" or "expired".

    Callers (api/routes.py's Moodle-connect endpoints) are responsible for
    the third real state, "never connected" — tracked via
    User.moodle_session_status being NULL, checked BEFORE this function is
    even called, since a user who has never completed the manual connect
    flow has no session worth checking (and a freshly-launched, never-
    logged-in Chromium profile directory is not reliably empty on disk —
    Chromium itself writes its own scaffold files on first launch
    regardless of login state — so "is the directory empty" is not a safe
    way to infer "never connected" here).

    Logs exactly the two lines Part 3 of the multi-user task asked for —
    "[MOODLE AUTH] Existing session valid — skipping login" or
    "[MOODLE AUTH] Session expired — requiring re-authentication" — and
    NEVER logs a password, cookie, access token, or session id; only this
    plain status string and the user id.

    This function's own browser context is always closed before
    returning, whether the check succeeds or raises — it must never be
    the thing holding a lock on this user's profile directory that a
    later real sync/connect then can't open.

    `headless` defaults to True (unchanged default behavior) but is
    forced to False when ESMERELDA_MOODLE_CHECK_HEADED=1 is set — a
    TEMPORARY, opt-in controlled-test override (see BUILD_LOG.md's
    session-lifecycle entry), never a change to normal behavior."""
    effective_headless = False if _FORCE_HEADED_SESSION_CHECK else headless
    profile_dir = get_user_browser_profile_dir(user_id)
    logger.info(
        "[MOODLE PROFILE TRACE]\noperation=session-check\nuser_id=%s\nprofile_path=%s",
        user_id, profile_dir,
    )
    logger.info(
        "[MOODLE SESSION LIFECYCLE]\nCHECK launch:\n  profile_path=%s\n  headless=%s",
        profile_dir, effective_headless,
    )

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=effective_headless,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(MOODLE_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
            except Exception as exc:
                logger.warning("[MOODLE AUTH] user=%s could not reach Moodle to check session: %s", user_id, exc)
                logger.info("[MOODLE AUTH] Session expired — requiring re-authentication (user=%s)", user_id)
                return "expired"

            logged_in = page.locator(_LOGGED_IN_MARKER_SELECTOR).count() > 0
            has_login_form = page.locator(_LOGIN_FORM_SELECTOR).count() > 0

            logger.info(
                "[MOODLE SESSION LIFECYCLE]\nCHECK loaded:\n  url=%s\n  title=%r\n  logged_in_marker=%s",
                page.url, page.title(), logged_in and not has_login_form,
            )
            logger.info(
                "[MOODLE SESSION LIFECYCLE]\nCHECK cookies:\n  cookies_count=%d\n  relevant_moodle_cookies=%s",
                len(context.cookies()), _cookie_names_only(context),
            )

            if logged_in and not has_login_form:
                logger.info("[MOODLE AUTH] Existing session valid — skipping login (user=%s)", user_id)
                return "connected"

            logger.info("[MOODLE AUTH] Session expired — requiring re-authentication (user=%s)", user_id)
            return "expired"
        finally:
            context.close()


def connect_user_interactively(user_id: int, *, timeout_seconds: int = 180) -> str:
    """The manual, real, per-user Google-SSO connect flow (Part 7): opens
    a REAL, VISIBLE browser window on whatever machine this backend
    process is running on, against this user's own persisted profile
    directory, and waits for the user to complete Google sign-in
    themselves — never touches a password or 2FA code programmatically.
    Polls for the same logged-in marker check_moodle_session() uses,
    every 2 seconds, up to timeout_seconds, so it returns as soon as the
    user finishes (rather than requiring a blocking input() prompt, which
    would hold this open across an HTTP request/response cycle).

    Returns "connected" if the user completed sign-in before the timeout,
    "timed_out" otherwise (the window is still left open in that case —
    an immediate retry of the connect flow will simply reuse this same
    partially-progressed session next time, not lose anything).

    KNOWN LIMITATION, stated plainly rather than glossed over (see
    BUILD_LOG.md): this opens a window on the BACKEND's own machine, not
    the end user's browser. That is fine for local/single-machine
    development and testing (which is what this batch's own test matrix
    exercises), but is NOT a viable mechanism for a real hosted, multi-
    tenant deployment where the backend runs on a server the student
    never has physical/display access to — a real production version of
    this flow would need a different mechanism entirely (e.g. a
    server-side headless SSO relay, or moving to Moodle Web Service
    tokens once FLAME's Moodle admin confirms student self-service token
    creation is enabled — see BUILD_LOG.md's earlier investigation
    entries). This function is the "cleanest minimal local/dev identity
    mechanism" Part 8 explicitly allows for now, not a claim that it's
    production-ready."""
    profile_dir = get_user_browser_profile_dir(user_id)
    logger.info(
        "[MOODLE PROFILE TRACE]\noperation=connect\nuser_id=%s\nprofile_path=%s",
        user_id, profile_dir,
    )
    logger.info(
        "[MOODLE SESSION LIFECYCLE]\nCONNECT launch:\n  profile_path=%s\n  headless=%s",
        profile_dir, False,
    )

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(MOODLE_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                logger.warning("[MOODLE AUTH] user=%s could not open Moodle for connect: %s", user_id, exc)

            logger.info("[MOODLE AUTH] user=%s connect flow opened — waiting for manual Google sign-in", user_id)

            elapsed = 0
            poll_interval = 2000
            while elapsed < timeout_seconds * 1000:
                page.wait_for_timeout(poll_interval)
                elapsed += poll_interval
                try:
                    logged_in = page.locator(_LOGGED_IN_MARKER_SELECTOR).count() > 0
                    has_login_form = page.locator(_LOGIN_FORM_SELECTOR).count() > 0
                except Exception:
                    continue
                if logged_in and not has_login_form:
                    logger.info(
                        "[MOODLE SESSION LIFECYCLE]\nCONNECT authenticated:\n  url=%s\n  title=%r\n  "
                        "logged_in_marker=%s",
                        page.url, page.title(), True,
                    )
                    logger.info(
                        "[MOODLE SESSION LIFECYCLE]\nCONNECT before close:\n  cookies_count=%d\n  "
                        "relevant_moodle_cookies=%s\n  local_storage_keys=%s",
                        len(context.cookies()), _cookie_names_only(context), _local_storage_keys_only(page),
                    )
                    logger.info("[MOODLE AUTH] user=%s completed manual sign-in — session persisted", user_id)
                    return "connected"

            logger.info("[MOODLE AUTH] user=%s connect flow timed out after %ds", user_id, timeout_seconds)
            return "timed_out"
        finally:
            context.close()
            logger.info(
                "[MOODLE SESSION LIFECYCLE]\nCONNECT after close:\n  profile_path=%s",
                profile_dir,
            )


def handle_dialog(dialog):
    print(f"\n[Moodle dialog] {dialog.message}")

    try:
        dialog.accept()
    except Exception:
        pass


def clean_course_name(text):
    """
    Cleans Moodle's course link text.
    """
    text = text.strip()

    # Remove the extra "Course name" text that appears
    # in some duplicate links.
    if text.startswith("Course name"):
        text = text.replace("Course name", "", 1).strip()

    return text

def get_authenticated_page():
    p = sync_playwright().start()

    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        viewport={"width": 1400, "height": 900},
    )

    page = context.pages[0] if context.pages else context.new_page()

    page.on("dialog", handle_dialog)

    page.goto(
        MOODLE_URL,
        wait_until="domcontentloaded",
        timeout=30000
    )

    page.wait_for_timeout(3000)

    if "login" in page.url.lower():
        print("Please log into Moodle.")
        input("Press ENTER after reaching your dashboard...")

        page.wait_for_timeout(2000)

        if "login" in page.url.lower():
            raise RuntimeError(
                "Login was not completed. Still on Moodle login page."
        )

    return p, context, page

def main():

    print("Starting Esmerelda Moodle connector...")

    with sync_playwright() as p:

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 900},
        )

        page = context.pages[0] if context.pages else context.new_page()

        page.on("dialog", handle_dialog)

        print("Opening FLAME Moodle...")

        try:
            page.goto(
                MOODLE_URL,
                wait_until="domcontentloaded",
                timeout=30000
            )
        except Exception as e:
            print("Navigation warning:", e)

        page.wait_for_timeout(3000)

        # --------------------------------------------------
        # LOGIN
        # --------------------------------------------------

        if "login" in page.url.lower():

            print("\nPlease log into FLAME Moodle normally.")

            input(
                "Press ENTER after you reach your Moodle dashboard... "
            )

            page.wait_for_timeout(3000)

        print("\n========================================")
        print("READING ACTIVE COURSES")
        print("========================================")

        print("Current URL:", page.url)

        # --------------------------------------------------
        # GET ALL COURSE LINKS
        # --------------------------------------------------

        links = page.locator('a[href*="/course/view.php"]')

        all_courses = {}

        try:

            count = links.count()

            print(f"\nMoodle course links found: {count}")

            for i in range(count):

                try:

                    link = links.nth(i)

                    text = clean_course_name(
                        link.inner_text()
                    )

                    href = link.get_attribute("href")

                    if not text or not href:
                        continue

                    # Only keep actual course URLs
                    if "/course/view.php" not in href:
                        continue

                    # Ignore empty/duplicate names
                    if text not in all_courses:
                        all_courses[text] = href

                except Exception:
                    continue

        except Exception as e:

            print("Could not read course links:", e)

        # --------------------------------------------------
        # GET ACTIVE COURSE NAMES FROM PAGE TEXT
        # --------------------------------------------------

        active_course_names = []

        try:

            body_text = page.locator("body").inner_text()

            lines = [
                line.strip()
                for line in body_text.splitlines()
                if line.strip()
            ]

            # Find the Moodle filter
            try:
                start = lines.index("Only courses in progress") + 1
            except ValueError:
                start = -1

            # "Course overview" marks the end of this section
            try:
                end = lines.index("Course overview")
            except ValueError:
                end = len(lines)

            if start != -1:

                active_course_names = lines[start:end]

        except Exception as e:

            print("Could not read active course section:", e)

        # --------------------------------------------------
        # MATCH ACTIVE COURSES TO THEIR URLS
        # --------------------------------------------------

        active_courses = []

        for name in active_course_names:

            if name in all_courses:

                active_courses.append({
                    "name": name,
                    "url": all_courses[name]
                })

        # --------------------------------------------------
        # DISPLAY RESULTS
        # --------------------------------------------------

        print("\n========================================")
        print(
            f"ACTIVE COURSES FOUND: {len(active_courses)}"
        )
        print("========================================")

        for index, course in enumerate(
            active_courses,
            start=1
        ):

            print(f"\n{index}. {course['name']}")
            print(f"   {course['url']}")

        # --------------------------------------------------
        # DEBUG INFORMATION
        # --------------------------------------------------

        if not active_courses:

            print("\n⚠ No active courses were matched.")

            print("\nDetected section text:")

            for line in active_course_names:
                print("-", line)

            print("\nAvailable course names:")

            for name in all_courses:
                print("-", name)

        else:

            print(
                "\n✓ Esmerelda successfully isolated "
                "your in-progress courses."
            )

        input(
            "\nPress ENTER to close the browser... "
        )

        context.close()


if __name__ == "__main__":
    main()
