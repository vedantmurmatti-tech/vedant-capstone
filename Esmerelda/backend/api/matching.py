"""Shared, deterministic name matching.

Used by the agents and by the Gemini tool layer (gemini_tools.py) so a
free-text reference like "Tangible Interfaces", "DESG322", or "the MVP
assignment" resolves to the same real `Course`/`Assignment` row the same
way regardless of which caller asks. Pure string matching — no fuzzy
scoring, no LLM call.
"""

from storage.models import Assignment, Course


def _normalize_compact(text: str) -> str:
    """Lowercased, with whitespace and hyphens removed — makes two names
    comparable regardless of incidental formatting differences (extra
    spaces, hyphen vs. en-dash, etc.), and is also what lets a truncated
    name safely substring-match a full one: removing separators doesn't
    change WHERE a real truncation cut the text, so a stored, truncated
    course name still appears as a contiguous prefix of the user's full,
    untruncated one (and vice versa) after this normalization."""
    return "".join(text.lower().split()).replace("-", "")


def match_course(query: str, courses: list[Course]) -> Course | None:
    """Resolves a free-text course reference to a real Course row via a
    deterministic, ordered set of real database-field comparisons — never
    an LLM guess. Tried in order of how unambiguous each signal is:

    1. Exact Moodle course id — the query contains the literal numeric id
       (e.g. an LLM or a UI element that already knows it from an earlier
       lookup passes it directly, rather than re-guessing a name match).
    2. Course short_name — an exact/substring match against Course.short_name
       when the sync actually captured one (may be None — see storage/crud.py).
    3. Course code prefix — the first "-"-separated segment of Course.name
       (e.g. "DESG319" from "DESG319-UGSEM5-2026/27S1-..."), Moodle's own
       course shortname convention baked into the fullname. Kept exactly
       as before — this already correctly matches on the reported query
       shape regardless of any truncation elsewhere in the name.
    4. Normalized full-name substring, tolerant of truncation on EITHER
       side — handles a long course fullname that Moodle's own dashboard
       truncated before this project ever scraped it (a real, observed
       behavior — see BUILD_LOG.md), or a user pasting only part of a
       long name.
    5. A single distinctive subject word from the end of the course name
       (e.g. "Interfaces" from "...-Tangible Interfaces") — the loosest,
       last-resort signal, kept as the final fallback exactly as before.

    Returns None (never guesses) when nothing matches any of these."""
    if not query or not query.strip():
        return None
    lower = query.lower()
    compact_query = _normalize_compact(query)

    # 1. Exact Moodle course id.
    digits = "".join(ch for ch in query if ch.isdigit())
    if digits:
        for course in courses:
            if course.moodle_id and course.moodle_id == digits:
                return course

    # 2. Course short_name.
    for course in courses:
        if course.short_name:
            short_compact = _normalize_compact(course.short_name)
            if short_compact and (short_compact in compact_query or compact_query in short_compact):
                return course

    # 3. Course code prefix (existing behavior, unchanged).
    for course in courses:
        parts = course.name.split("-")
        code = parts[0].strip().lower() if parts else ""
        if code and code.replace(" ", "") in lower.replace(" ", "").replace("-", ""):
            return course

    # 4. Normalized full-name substring, tolerant of truncation either way.
    # The reverse direction (compact_query in name_compact) is gated on a
    # minimum length so a short, generic query (a stray word, a bare
    # short number) can't trivially substring-match into an unrelated
    # course's long name — the forward direction (a real, already-long
    # course name found inside the user's query) needs no such gate,
    # since a genuine course name is never that short to begin with.
    for course in courses:
        name_compact = _normalize_compact(course.name)
        if not name_compact:
            continue
        if name_compact in compact_query:
            return course
        if len(compact_query) > 5 and compact_query in name_compact:
            return course

    # 5. Subject/topic word fallback (existing behavior, unchanged).
    for course in courses:
        parts = course.name.split("-")
        subject = parts[-1].strip().lower() if parts else ""
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
