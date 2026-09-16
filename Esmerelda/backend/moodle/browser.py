from pathlib import Path
from playwright.sync_api import sync_playwright

MOODLE_URL = "https://lms.flame.edu.in"
PROFILE_DIR = Path(__file__).parent / "browser_profile"


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
