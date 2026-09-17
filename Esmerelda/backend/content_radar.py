import json
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright
import re
from storage.database import init_db
from storage.crud import save_resource
import hashlib

MOODLE_URL = "https://lms.flame.edu.in"

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "moodle" / "browser_profile"
STATE_DIR = BASE_DIR / "state"
SNAPSHOT_FILE = STATE_DIR / "content_snapshot.json"

import json


def load_snapshot():
    if not SNAPSHOT_FILE.exists():
        return []

    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return []


def save_snapshot(items):
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as file:
        json.dump(items, file, indent=4, ensure_ascii=False)


def find_new_items(old_items, current_items):
    old_keys = {
        (item["name"], item["url"])
        for item in old_items
    }

    return [
        item
        for item in current_items
        if (item["name"], item["url"]) not in old_keys
    ]
def generate_resource_id(url: str) -> str:
    if "id=" in url:
        return url.split("id=")[-1].split("&")[0]

    return "url_" + hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()[:16]
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

        print("Please log into Moodle.")
        print("Waiting for login...")

        page.wait_for_url(
            re.compile(r"^(?!.*login).*"),
            timeout=180000
        )

        page.wait_for_timeout(3000)

    return context, page


# ============================================================
# COURSE CONTENT
# ============================================================

def extract_course_content(page, course):

    print("\n")
    print("=" * 70)
    print("COURSE")
    print("=" * 70)
    print(course["name"])

    page.goto(
        course["url"],
        wait_until="domcontentloaded"
    )

    page.wait_for_timeout(3000)

    print("URL:", page.url)

    # --------------------------------------------------------
    # Find course sections
    # --------------------------------------------------------

    sections = page.locator(
        "li.section"
    )

    print("\nSections found:", sections.count())

    items = []

    for section_index in range(sections.count()):

        section = sections.nth(section_index)

        try:

            if not section.is_visible():
                continue

            # ------------------------------------------------
            # Section name
            # ------------------------------------------------

            section_name = "Unknown Section"

            section_headings = section.locator(
                ".sectionname, .sectionname a, h3, h4"
            )

            if section_headings.count() > 0:

                for h in range(section_headings.count()):

                    try:
                        text = section_headings.nth(h).inner_text().strip()

                        if text:
                            section_name = text
                            break
                    except:
                        pass

            # ------------------------------------------------
            # Resources / activities
            # ------------------------------------------------

            activity_links = section.locator(
                "a[href]"
            )

            for i in range(activity_links.count()):

                link = activity_links.nth(i)

                try:

                    if not link.is_visible():
                        continue

                    name = link.inner_text().strip()
                    href = link.get_attribute("href")

                    if not name or not href:
                        continue

                    # Ignore navigation links.
                    if any(x in href for x in [
                        "/user/",
                        "/course/view.php",
                        "/mod/forum/view.php",
                        "/grade/",
                        "/message/",
                        "/calendar/"
                    ]):
                        continue

                    # Ignore generic controls.
                    if name.lower() in [
                        "more",
                        "edit",
                        "hide",
                        "show",
                        "settings"
                    ]:
                        continue

                    # ----------------------------------------
                    # Determine resource type
                    # ----------------------------------------

                    lower_href = href.lower()
                    lower_name = name.lower()

                    if ".pdf" in lower_href:
                        item_type = "PDF"

                    elif ".ppt" in lower_href or ".pptx" in lower_href:
                        item_type = "PowerPoint"

                    elif ".doc" in lower_href or ".docx" in lower_href:
                        item_type = "Word Document"

                    elif ".xls" in lower_href or ".xlsx" in lower_href:
                        item_type = "Spreadsheet"

                    elif "/mod/assign/" in lower_href:
                        item_type = "Assignment"

                    elif "/mod/quiz/" in lower_href:
                        item_type = "Quiz"

                    elif "/mod/resource/" in lower_href:
                        item_type = "Resource"

                    elif "/mod/page/" in lower_href:
                        item_type = "Page"

                    elif "/mod/url/" in lower_href:
                        item_type = "Link"

                    elif "/mod/forum/" in lower_href:
                        item_type = "Forum"

                    elif (
                        "youtube" in lower_href
                        or "youtu.be" in lower_href
                        or "vimeo" in lower_href
                    ):
                        item_type = "Video"

                    else:
                        item_type = "Activity"

                    item = {
                        "course": course["name"],
                        "course_url": course["url"],
                        "section": section_name,
                        "name": name,
                        "type": item_type,
                        "url": href
                    }

                    # ----------------------------------------
                    # Deduplicate
                    # ----------------------------------------

                    duplicate = False

                    for existing in items:

                        if (
                            existing["name"] == item["name"]
                            and existing["url"] == item["url"]
                        ):
                            duplicate = True
                            break

                    if not duplicate:
                        items.append(item)

                except Exception as e:

                    print(
                        "Could not process activity:",
                        e
                    )

        except Exception as e:

            print(
                "Could not process section:",
                e
            )

    return items


# ============================================================
# PRINT RESULTS
# ============================================================

def print_course_items(items):

    print("\n")

    if not items:
        print("No course content detected.")
        return

    current_section = None

    for item in items:

        if item["section"] != current_section:

            current_section = item["section"]

            print("\n----------------------------------------")
            print(current_section)
            print("----------------------------------------")

        print(
            f"[{item['type']}] "
            f"{item['name']}"
        )

        print(
            f"    {item['url']}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    with sync_playwright() as p:

        context, page = open_moodle(p)

        try:

            courses = [
                {
                    "name": "BUAN301-UGSEM5B-2026/27S1-Statistical Data Analysis and Visualisation",
                    "url": "https://lms.flame.edu.in/course/view.php?id=24527"
                },
                {
                    "name": "BUAN302-UGSEM5B-2026/27S1-Machine Learning - 1: Introduction",
                    "url": "https://lms.flame.edu.in/course/view.php?id=24529"
                },
                {
                    "name": "DESG310-UGSEM5A-2026/27S1-Game Design",
                    "url": "https://lms.flame.edu.in/course/view.php?id=24443"
                },
                {
                    "name": "DESG319-UGSEM5-2026/27S1-Introduction to Artificial Intelligence & Machine Lear",
                    "url": "https://lms.flame.edu.in/course/view.php?id=24444"
                },
                {
                    "name": "DESG320-UGSEM5A-2026/27S1-Service Design",
                    "url": "https://lms.flame.edu.in/course/view.php?id=24445"
                },
                {
                    "name": "DESG322-UGSEM5-2026/27S1-Tangible Interfaces",
                    "url": "https://lms.flame.edu.in/course/view.php?id=24446"
                },
                {
                    "name": "VATS301-UGSEM3-2026/27S1-Technology and Society",
                    "url": "https://lms.flame.edu.in/course/view.php?id=24834"
                }
            ]

            all_items = []

            for course in courses:

                items = extract_course_content(
                    page,
                    course
                )
                for item in items:
                    resource_id = generate_resource_id(item["url"])

                    save_resource(
                        moodle_id=resource_id,
                        course_name=course["name"],
                        name=item["name"],
                        resource_type=item["type"],
                        url=item["url"],
                        description=item["section"]
                    )
                print_course_items(items)

                all_items.extend(items)

            print("\n")
            print("=" * 70)
            print("TOTAL CONTENT ITEMS:", len(all_items))
            print("=" * 70)

            # ====================================================
            # CHANGE DETECTION
            # ====================================================

            old_items = load_snapshot()

            print("\n")
            print("=" * 70)
            print("CHANGE DETECTION")
            print("=" * 70)

            if not old_items:

                print("No previous snapshot found.")
                print("This is the first scan.")

                save_snapshot(all_items)

                print(
                    f"Saved {len(all_items)} items "
                    "as the initial snapshot."
                )

            else:

                new_items = find_new_items(
                    old_items,
                    all_items
                )

                print(
                    f"Previous items: {len(old_items)}"
                )

                print(
                    f"Current items: {len(all_items)}"
                )

                print(
                    f"NEW ITEMS: {len(new_items)}"
                )

                if new_items:

                    print("\nNEW CONTENT:")
                    print("-" * 70)

                    for item in new_items:

                        print(
                            f"[{item['type']}] "
                            f"{item['name']}"
                        )

                        print(
                            f"Course: {item['course']}"
                        )

                        print(
                            f"Section: {item['section']}"
                        )

                        print(
                            f"URL: {item['url']}"
                        )

                        print()

                else:

                    print("\nNo new content found.")

                save_snapshot(all_items)

                print("\nSnapshot updated.")

        finally:

            context.close()


if __name__ == "__main__":
    main()
