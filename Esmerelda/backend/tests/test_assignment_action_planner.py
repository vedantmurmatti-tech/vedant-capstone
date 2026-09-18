"""Local tests for the redesigned assignment-action-planner Skill
(backend/api/skills/assignment_action_planner/__init__.py). No LLM calls —
pure deterministic Python, exercised directly. Uses the real database for
the representative Assessment 2 case, plus constructed (unpersisted)
Assignment/Course/Resource objects for edge cases the real dataset doesn't
happen to cover (e.g. a requirement with zero local evidence anywhere).

Not a pytest suite (pytest isn't installed) — plain assert-style checks,
matching backend/tests/test_groq_agent.py's convention.

Run with:
    venv/Scripts/python.exe tests/test_assignment_action_planner.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import queries
from api.skills.assignment_action_planner import extract_requirements, plan_for_assignment, render_plan
from storage.database import SessionLocal
from storage.models import Assignment, Course, Resource

results: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    results.append((name, status))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


def get_real_assessment_2():
    db = SessionLocal()
    row = (
        db.query(Assignment, Course)
        .join(Course, Assignment.course_id == Course.id)
        .filter(Assignment.name.like("Assessment 2%"))
        .first()
    )
    return db, *row


# --- 1. requirements are extracted ------------------------------------------


def test_requirements_extracted() -> None:
    db, assignment, course = get_real_assessment_2()

    phrases, source = extract_requirements(assignment)
    check(
        "1. Requirements extracted from the real assignment title",
        phrases == ["Custom Skill", "an agent", "1-2 MCP", "chained into one workflow"]
        and "no description synced" in source,
        f"phrases={phrases}",
    )

    # A description, when present, is preferred over the title.
    with_description = Assignment(
        id=999,
        moodle_id="TEST-1",
        course_id=course.id,
        name="Assessment X: placeholder title",
        description="Write a report and submit a video and include a bibliography.",
        due_date=None,
        submission_url=None,
    )
    phrases2, source2 = extract_requirements(with_description)
    check(
        "1b. Requirements extracted from assignment.description when present",
        phrases2 == ["Write a report", "submit a video", "include a bibliography"]
        and source2 == "assignment.description",
        f"phrases={phrases2}, source={source2}",
    )
    db.close()


# --- 2. actionable steps are generated --------------------------------------


def test_actionable_steps_generated() -> None:
    db, assignment, course = get_real_assessment_2()
    matched_resource = queries.fetch_resource_by_moodle_id(db, assignment.moodle_id)
    related_documents = (
        queries.fetch_documents_for_resource(db, matched_resource.id) if matched_resource else []
    )
    plan = plan_for_assignment(
        assignment, course, matched_resource=matched_resource, related_documents=related_documents
    )

    all_have_real_content = all(
        r.next_action.strip() and r.expected_deliverable.strip() and r.verification_method.strip() and r.evidence.strip()
        for r in plan.requirements
    )
    valid_statuses = all(r.status in ("completed", "incomplete", "unverified") for r in plan.requirements)
    check(
        "2. Actionable steps generated for every requirement",
        len(plan.requirements) == 4 and all_have_real_content and valid_statuses,
        f"statuses={[r.status for r in plan.requirements]}",
    )

    rendered = render_plan(plan)
    check(
        "2b. render_plan() produces a full multi-section plan, not just urgency+link",
        "Next action:" in rendered and "Expected deliverable:" in rendered and "Verify by:" in rendered,
    )
    db.close()


# --- 3. missing information is identified -----------------------------------


def test_missing_information_identified() -> None:
    db, assignment, course = get_real_assessment_2()
    matched_resource = queries.fetch_resource_by_moodle_id(db, assignment.moodle_id)
    related_documents = (
        queries.fetch_documents_for_resource(db, matched_resource.id) if matched_resource else []
    )
    plan = plan_for_assignment(
        assignment, course, matched_resource=matched_resource, related_documents=related_documents
    )

    items = {m.item for m in plan.missing_information}
    check(
        "3. Missing information identified — real, specific gaps, not invented data",
        "Assignment description text" in items and any("downloaded document" in i for i in items),
        f"missing_information items={items}",
    )
    db.close()


# --- 4. source grounding is preserved ----------------------------------------


def test_source_grounding_preserved() -> None:
    db, assignment, course = get_real_assessment_2()
    plan = plan_for_assignment(assignment, course)

    check(
        "4. Source grounding preserved — real Moodle IDs, due date, submission URL",
        plan.grounding.submission_url == assignment.submission_url
        and plan.grounding.moodle_assignment_id == assignment.moodle_id
        and plan.grounding.moodle_course_id == course.moodle_id
        and plan.due_date == assignment.due_date.isoformat(),
        f"submission_url={plan.grounding.submission_url}",
    )
    db.close()


# --- 5. useful output even when no documents are available -----------------


def test_useful_without_documents() -> None:
    course = Course(id=1, moodle_id="C1", name="Test Course", short_name="TC", description=None)
    assignment = Assignment(
        id=1000,
        moodle_id="A1000",
        course_id=1,
        name="Assessment Z: Zqxjklw Nonexistent Requirement Phrase",
        description=None,
        due_date=None,
        submission_url="https://lms.example.edu/mod/assign/view.php?id=1000",
    )

    # No matched resource, no documents at all — the worst real-world case.
    plan = plan_for_assignment(assignment, course, matched_resource=None, related_documents=[])

    check(
        "5. Useful output with zero resources/documents available",
        len(plan.requirements) >= 1
        and all(r.status == "unverified" for r in plan.requirements)
        and all(r.next_action.strip() for r in plan.requirements)
        and any("matching Moodle resource" in m.item for m in plan.missing_information),
        f"requirements={[(r.requirement, r.status) for r in plan.requirements]}",
    )

    rendered = render_plan(plan)
    check(
        "5b. render_plan() still produces a complete, non-empty plan",
        len(rendered) > 100 and "Source:" in rendered,
    )


# --- bonus: the "incomplete" bucket triggers for real (resource exists, no doc) --


def test_incomplete_bucket_real_example() -> None:
    # Constructed rather than read from BUILD_LOG.md-dependent real data: the
    # earlier version of this test relied on Assessment 2's real title
    # phrase "chained into one workflow" happening to have no local
    # evidence yet — but BUILD_LOG.md keeps growing with real work
    # (including this very feature), so that phrase can legitimately gain
    # matching evidence over time and correctly flip to "completed". A
    # deliberately unique, nonsense phrase keeps this test's premise (no
    # local evidence exists) true regardless of how much BUILD_LOG.md grows.
    course = Course(id=2, moodle_id="C2", name="Test Course 2", short_name="TC2", description=None)
    resource = Resource(
        id=1,
        moodle_id="R1",
        course_id=2,
        name="Zqxjklw Vorplex Assignment Page",
        resource_type="Assignment",
        url="https://lms.example.edu/mod/assign/view.php?id=2000",
        description=None,
    )
    assignment = Assignment(
        id=2000,
        moodle_id="R1",
        course_id=2,
        name="Assessment Y: Zqxjklw Vorplex",
        description=None,
        due_date=None,
        submission_url="https://lms.example.edu/mod/assign/view.php?id=2000",
    )
    plan = plan_for_assignment(assignment, course, matched_resource=resource, related_documents=[])

    has_incomplete = any(r.status == "incomplete" for r in plan.requirements)
    check(
        "Bonus: 'incomplete' status reachable (resource found, no document synced, no local evidence)",
        has_incomplete,
        f"statuses={[r.status for r in plan.requirements]}",
    )


def main() -> None:
    test_requirements_extracted()
    test_actionable_steps_generated()
    test_missing_information_identified()
    test_source_grounding_preserved()
    test_useful_without_documents()
    test_incomplete_bucket_real_example()

    print("\n--- Summary ---")
    failures = [r for r in results if r[1] == "FAIL"]
    print(f"{len(results) - len(failures)}/{len(results)} passed")
    if failures:
        print("FAILURES:", failures)
        sys.exit(1)


if __name__ == "__main__":
    main()
