"""Local tests for deterministic follow-up-prompt generation
(backend/api/followups.py) and its wiring into deterministic_agent.py. No
LLM calls anywhere — generate_followups() is pure Python, and the
deterministic-agent tests exercise the real rule-based agent directly
(which itself never calls an LLM).

Not a pytest suite (pytest isn't installed) — plain assert-style checks,
matching the existing convention.

Run with:
    venv/Scripts/python.exe tests/test_followups.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.followups import generate_followups
from api.schemas import AssignmentOut, CourseOut, DocumentOut

results: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    results.append((name, status))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


def make_assignment(**overrides) -> AssignmentOut:
    base = dict(
        id=1,
        moodleId="A1",
        courseId=1,
        courseName="DESG319-UGSEM5-2026/27S1-Introduction to AI",
        courseShortName="DESG319",
        name="Assessment 2: MVP",
        description=None,
        dueDate=None,
        submissionUrl="https://lms.example.edu/mod/assign/view.php?id=1",
    )
    base.update(overrides)
    return AssignmentOut(**base)


def make_course(**overrides) -> CourseOut:
    base = dict(id=1, moodleId="C1", name="DESG319-UGSEM5-Introduction to AI", shortName="DESG319", description=None)
    base.update(overrides)
    return CourseOut(**base)


def make_document(**overrides) -> DocumentOut:
    base = dict(
        id=1,
        resourceId=1,
        courseId=1,
        courseName="DESG319-UGSEM5-Introduction to AI",
        name="notes.pdf",
        fileType="pdf",
        updatedAt=None,
        indexingStatus="indexed",
        downloadUrl="/api/documents/1/download",
    )
    base.update(overrides)
    return DocumentOut(**base)


# --- 1. assignment-related follow-ups ---------------------------------------


def test_assignment_related_followups() -> None:
    a = make_assignment()
    followups = generate_followups(
        user_message="what's due this week?",
        reply_text="Assessment 2: MVP is due soon.",
        assignments=[a],
        courses=[],
        documents=[],
    )
    check(
        "1. Assignment-related follow-ups reference the real course, not invented data",
        1 <= len(followups) <= 4
        and any("DESG319" in f for f in followups)
        and all(f.strip() for f in followups),
        f"followups={followups}",
    )

    # Multiple assignments (list mode) still produces real, course-anchored suggestions.
    a2 = make_assignment(id=2, name="Assessment 3: Final", courseShortName="BUAN301", courseName="BUAN301-Stats")
    followups2 = generate_followups(
        user_message="what's due this week?",
        reply_text="Here's your priority plan...",
        assignments=[a, a2],
        courses=[],
        documents=[],
    )
    check(
        "1b. Multiple assignments still produce real, non-fabricated follow-ups",
        len(followups2) >= 1 and any("DESG319" in f for f in followups2),
        f"followups={followups2}",
    )


# --- 2. MCP-related follow-ups ----------------------------------------------


def test_mcp_related_followups() -> None:
    followups = generate_followups(
        user_message="how many documents are indexed?",
        reply_text="There are 34 documents indexed across all courses.",
        assignments=[],
        courses=[],
        documents=[],
        used_mcp=True,
    )
    check(
        "2. MCP-related follow-up suggested when a real MCP call happened",
        any("aggregate" in f.lower() for f in followups),
        f"followups={followups}",
    )

    followups_no_mcp = generate_followups(
        user_message="how many documents are indexed?",
        reply_text="There are 34 documents indexed across all courses.",
        assignments=[],
        courses=[],
        documents=[],
        used_mcp=False,
    )
    check(
        "2b. No MCP follow-up suggested when MCP wasn't actually used",
        not any("aggregate" in f.lower() for f in followups_no_mcp),
        f"followups={followups_no_mcp}",
    )


# --- 3. generic responses (no tool context) ---------------------------------


def test_generic_response_no_context() -> None:
    followups = generate_followups(
        user_message="hi there",
        reply_text="Hello! I'm Esmerelda, your academic assistant.",
        assignments=[],
        courses=[],
        documents=[],
    )
    check(
        "3. A plain conversational reply with no tool results produces no fabricated follow-ups",
        followups == [],
        f"followups={followups}",
    )


# --- 4. empty follow-up results (real 'not found' case) ---------------------


def test_not_found_recovery_followups() -> None:
    followups = generate_followups(
        user_message="what should I do about the Quantum Basket Weaving assignment?",
        reply_text="I couldn't find any assignment matching 'Quantum Basket Weaving'.",
        assignments=[],
        courses=[],
        documents=[],
    )
    check(
        "4. A real 'not found' reply gets safe, real recovery suggestions, not an empty result",
        1 <= len(followups) <= 4 and all(f.strip() for f in followups),
        f"followups={followups}",
    )

    # And confirm the truly-empty case is still possible (no context, no error signal).
    empty_case = generate_followups(
        user_message="thanks!",
        reply_text="You're welcome!",
        assignments=[],
        courses=[],
        documents=[],
    )
    check(
        "4b. Empty list returned rather than fabricated suggestions when nothing is contextual",
        empty_case == [],
        f"followups={empty_case}",
    )


# --- dedup / no-duplicate check ----------------------------------------------


def test_no_duplicates_and_not_echoing_user_message() -> None:
    a = make_assignment()
    c = make_course()
    followups = generate_followups(
        user_message="Tell me more about DESG319",
        reply_text="Here's DESG319's info.",
        assignments=[a],
        courses=[c],
        documents=[],
    )
    lowered = [f.lower() for f in followups]
    check(
        "Bonus: no duplicate suggestions, and the user's own message isn't echoed back",
        len(lowered) == len(set(lowered)) and "tell me more about desg319" not in lowered,
        f"followups={followups}",
    )


def test_deterministic_agent_uses_shared_generator() -> None:
    from api.auth import get_or_create_demo_user
    from api.deterministic_agent import handle_chat_message_deterministic
    from storage.database import SessionLocal

    db = SessionLocal()
    response = handle_chat_message_deterministic(db, "what's due this week?", get_or_create_demo_user())
    db.close()

    check(
        "Bonus: deterministic_agent's real chat response includes real follow-ups (or none)",
        isinstance(response.followUps, list) and len(response.followUps) <= 4,
        f"followUps={response.followUps}",
    )


def main() -> None:
    test_assignment_related_followups()
    test_mcp_related_followups()
    test_generic_response_no_context()
    test_not_found_recovery_followups()
    test_no_duplicates_and_not_echoing_user_message()
    test_deterministic_agent_uses_shared_generator()

    print("\n--- Summary ---")
    failures = [r for r in results if r[1] == "FAIL"]
    print(f"{len(results) - len(failures)}/{len(results)} passed")
    if failures:
        print("FAILURES:", failures)
        sys.exit(1)


if __name__ == "__main__":
    main()
