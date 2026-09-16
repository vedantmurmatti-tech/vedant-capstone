from pathlib import Path
from playwright.sync_api import sync_playwright
import re

MOODLE_URL = "https://lms.flame.edu.in"

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "moodle" / "browser_profile"


def handle_dialog(dialog):
    print(f"Moodle dialog: {dialog.message}")
    dialog.accept()


def open_moodle(playwright):

    context = playwright.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=False
    )

    context.on("dialog", handle_dialog)

    page = context.pages[0] if context.pages else context.new_page()

    page.goto(
        MOODLE_URL,
        wait_until="domcontentloaded"
    )

    page.wait_for_timeout(4000)

    if "login" in page.url.lower():

        print("\nPlease log into Moodle.")
        print("Waiting for login...\n")

        page.wait_for_url(
            re.compile(r"^(?!.*login).*"),
            timeout=180000
        )

        page.wait_for_timeout(3000)

    return context, page


def get_active_courses(page):

    print("\n========================================")
    print("FINDING ACTIVE COURSES")
    print("========================================")

    # Moodle dashboard has a section containing:
    #
    # Only courses in progress
    #
    # We locate that text and then inspect the surrounding
    # dashboard area.

    active_toggle = page.get_by_text(
        "Only courses in progress",
        exact=True
    )

    print(
        "Active-course filter found:",
        active_toggle.count()
    )

    if active_toggle.count() == 0:
        print("Could not find active course filter.")
        return []

    # Find course links on the page.
    course_links = page.locator(
        'a[href*="/course/view.php?id="]'
    )

    all_courses = []

    for i in range(course_links.count()):

        link = course_links.nth(i)

        try:

            if not link.is_visible():
                continue

            name = link.inner_text().strip()
            href = link.get_attribute("href")

            if not name or not href:
                continue

            all_courses.append({
                "name": name,
                "url": href
            })

        except:
            continue

    # The dashboard text structure gives us the current
    # active-course names. We use the known current-term
    # course-code pattern to distinguish them from the
    # historical course list.

    active_courses = []

    current_pattern = re.compile(
        r"^(BUAN|DESG|VATS)\d+.*2026/27S1"
    )

    for course in all_courses:

        if current_pattern.search(course["name"]):

            if course["name"] not in [
                c["name"] for c in active_courses
            ]:

                active_courses.append(course)

    print("\nACTIVE COURSES FOUND:")
    print("----------------------------------------")

    for i, course in enumerate(active_courses, 1):

        print(f"{i}. {course['name']}")
        print(f"   {course['url']}")

    print("----------------------------------------")
    print(
        f"TOTAL ACTIVE COURSES: {len(active_courses)}"
    )

    return active_courses


def main():

    with sync_playwright() as p:

        context, page = open_moodle(p)

        try:

            courses = get_active_courses(page)

            print("\n========================================")
            print("COURSE RADAR COMPLETE")
            print("========================================")

        finally:

            context.close()


if __name__ == "__main__":
    main()
