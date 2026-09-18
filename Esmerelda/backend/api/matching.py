"""Shared, deterministic name matching.

Used by the agents and by the Gemini tool layer (gemini_tools.py) so a
free-text reference like "Tangible Interfaces", "DESG322", or "the MVP
assignment" resolves to the same real `Course`/`Assignment` row the same
way regardless of which caller asks. Pure string matching — no fuzzy
scoring, no LLM call.
"""

from storage.models import Assignment, Course


def match_course(query: str, courses: list[Course]) -> Course | None:
    lower = query.lower()
    for course in courses:
        parts = course.name.split("-")
        code = parts[0].strip().lower() if parts else ""
        subject = parts[-1].strip().lower() if parts else ""
        if code and code.replace(" ", "") in lower.replace(" ", "").replace("-", ""):
            return course
        if subject and len(subject) > 3 and subject in lower:
            return course
    return None


def match_assignment(query: str, assignments: list[Assignment]) -> Assignment | None:
    """Case-insensitive substring match against real assignment names.

    Not semantic search — see the Limitations section of
    api/skills/assignment_action_planner/SKILL.md.
    """
    lower = query.lower().strip()
    if not lower:
        return None
    for assignment in assignments:
        if lower in assignment.name.lower() or assignment.name.lower() in lower:
            return assignment
    return None
