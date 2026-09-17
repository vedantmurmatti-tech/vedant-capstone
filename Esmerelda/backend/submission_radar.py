from ast import pattern
from datetime import datetime
from storage.database import init_db
from storage.crud import save_assignment
from pathlib import Path
from playwright.sync_api import sync_playwright
import pyttsx3
import re

MOODLE_URL = "https://lms.flame.edu.in"

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "moodle" / "browser_profile"


# ============================================================
# VOICE
# ============================================================

def speak(text):
    print(f"\nESMERELDA:\n{text}\n")

    engine = pyttsx3.init()
    engine.setProperty("rate", 175)
    engine.setProperty("volume", 1.0)

    engine.say(text)
    engine.runAndWait()


# ============================================================
# MOODLE
# ============================================================

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

        print("\nPlease log into Moodle in the browser.")
        print("Esmerelda will continue after login.\n")

        page.wait_for_url(
            re.compile(r"^(?!.*login).*"),
            timeout=180000
        )

        page.wait_for_timeout(3000)

    return context, page


# ============================================================
# TIMELINE
# ============================================================

def get_timeline(page):

    # We discovered this from the actual FLAME Moodle DOM.
    timeline = page.locator("section.block_timeline").first

    if timeline.count() == 0:
        timeline = page.locator(".block_timeline").first

    if timeline.count() == 0:
        print("Timeline block not found.")
        return None

    print("\nTimeline block found.")

    return timeline

def extract_due_date(text, assignment):
    pattern = re.compile(
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
        r"(\d{1,2}\s+\w+\s+\d{4})\s+"
        r"(\d{1,2}:\d{2})",
        re.IGNORECASE
    )

    assignment_position = text.find(assignment)

    if assignment_position == -1:
        return None

    matches = list(
        pattern.finditer(text[:assignment_position])
    )

    if not matches:
        return None

    match = matches[-1]

    date_text = f"{match.group(2)} {match.group(3)}"

    try:
        return datetime.strptime(
            date_text,
            "%d %B %Y %H:%M"
        )
    except ValueError:
        return None
# ============================================================
# EXTRACT TIMELINE
# ============================================================

def extract_timeline(page):

    timeline = get_timeline(page)

    if timeline is None:
        return []

    # Wait briefly in case Moodle loads the timeline asynchronously.
    try:
        timeline.wait_for(state="visible", timeout=10000)
    except:
        pass

    page.wait_for_timeout(2000)

    timeline_text = timeline.inner_text()

    print("\n========================================")
    print("TIMELINE CONTENT")
    print("========================================")
    print(timeline_text)
    print("========================================\n")

    events = []

    # Find actual links inside ONLY the Timeline block.
    links = timeline.locator("a")

    for i in range(links.count()):

        link = links.nth(i)

        try:
            if not link.is_visible():
                continue

            text = link.inner_text().strip()

            if not text:
                continue

            if not re.search(r"\bis due\b", text, re.I):
                continue

            href = link.get_attribute("href")

            assignment = re.sub(
                r"\s+is due\s*$",
                "",
                text,
                flags=re.I
            ).strip()

            event = {
                "assignment": assignment,
                "url": href,
                "course": None,
                "raw_text": text,
                "due_date": None
            }

            # Walk upward from the assignment link.
            parent = link

            for level in range(8):

                parent = parent.locator("..")

                try:
                    parent_text = parent.inner_text().strip()
                    if event["due_date"] is None:
                        parsed_date = extract_due_date(
                            parent_text,
                            event["assignment"]
)

                        if parsed_date:
                            

                            event["due_date"] = parsed_date
                except Exception as error:
                    
                    break
                    
                    
                    
                # Look for course links inside this event container.
                course_links = parent.locator(
                    'a[href*="/course/view.php"]'
                )

                if course_links.count() > 0:

                    for j in range(course_links.count()):

                        course_link = course_links.nth(j)

                        try:
                            course_name = course_link.inner_text().strip()

                            if course_name:
                                event["course"] = course_name
                                break

                        except:
                            continue
                if not event["course"]:

                    course_pattern = re.compile(
                        r"^(BUAN|DESG|VATS)\d+.*2026/27S1.*$",
                        re.MULTILINE
                )

                course_match = course_pattern.search(parent_text)

                if course_match:
                    event["course"] = course_match.group(0).strip()
        except Exception as error:
            print(f"DEBUG ERROR at level {level}: {error}")
            break
        events.append(event)

    return deduplicate(events)
                


# ============================================================
# DEDUPLICATION
# ============================================================

def deduplicate(events):

    unique = []
    seen = set()

    for event in events:

        key = (
            event["assignment"].lower(),
            (event["course"] or "").lower()
        )

        if key not in seen:
            seen.add(key)
            unique.append(event)

    return unique


# ============================================================
# BRIEFING
# ============================================================

def create_briefing(events):

    if not events:
        return (
            "I couldn't find any upcoming submissions "
            "in your Moodle timeline."
        )

    if len(events) == 1:

        event = events[0]

        if event["course"]:
            return (
                f"You have one upcoming submission. "
                f"{event['assignment']}, for "
                f"{clean_course_name(event['course'])}."
            )

        return (
            f"You have one upcoming submission. "
            f"{event['assignment']}."
        )

    message = (
        f"You have {len(events)} upcoming submissions. "
    )

    for index, event in enumerate(events, start=1):

        course = event["course"]

        if course:
            message += (
                f"Number {index}: "
                f"{event['assignment']}, for "
                f"{clean_course_name(course)}. "
            )
        else:
            message += (
                f"Number {index}: "
                f"{event['assignment']}. "
            )

    return message


def clean_course_name(course):

    # Convert:
    #
    # DESG322-UGSEM5-2026/27S1-Tangible Interfaces
    #
    # into:
    #
    # Tangible Interfaces

    parts = course.split("-")

    if len(parts) >= 4:
        return "-".join(parts[3:]).strip()

    return course

def save_events_to_database(events):
    init_db()

    for event in events:
        url = event.get("url")

        if not url:
            print(f"Skipping assignment without URL: {event['assignment']}")
            continue

        match = re.search(r"id=(\d+)", url)

        if not match:
            print(f"Could not find Moodle ID: {event['assignment']}")
            continue

        moodle_id = match.group(1)

        saved_assignment = save_assignment(
        moodle_id=moodle_id,
        course_name=event["course"],
        name=event["assignment"],
        submission_url=url,
        due_date=event["due_date"]
    )

        if saved_assignment:
            print(f"Saved assignment: {saved_assignment.name}")
# ============================================================
# MAIN
# ============================================================

def main():

    speak(
        "Good evening. Esmerelda is online. "
        "Let me check your academic deadlines."
    )

    with sync_playwright() as p:

        context = None

        try:

            context, page = open_moodle(p)

            events = extract_timeline(page)
            save_events_to_database(events)

            print("\n========================================")
            print(f"UNIQUE TIMELINE SUBMISSIONS: {len(events)}")
            print("========================================")

            for event in events:

                print("\nAssignment:", event["assignment"])
                print("Course:", event["course"])
                print("URL:", event["url"])
                print("Due date:", event["due_date"])

            print("\n========================================")

            briefing = create_briefing(events)

            speak(briefing)

        finally:

            if context:
                context.close()


if __name__ == "__main__":
    main()
