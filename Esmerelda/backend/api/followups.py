"""Deterministic follow-up-prompt generation — no extra LLM call.

Shared by groq_agent.py, gemini_agent.py, and deterministic_agent.py so all
three chat paths produce follow-up chips the same way, from the same kind
of context every agent already has after answering: the real
`AssignmentOut`/`CourseOut`/`DocumentOut` rows a tool call actually
returned (via `gemini_tools.ToolCollector`), whether a real MCP call
happened, the reply text, and the user's own message. Never invents an
assignment/course/date that wasn't actually returned this turn, and
returns `[]` rather than padding with filler when nothing contextual is
available.
"""

from .schemas import AssignmentOut, CourseOut, DocumentOut

MAX_FOLLOWUPS = 4

# Substrings that show up across the three agents' real "not found" /
# "nothing here" replies (tool-layer error dicts get paraphrased by the
# LLMs, and deterministic_agent.py's own fallback text) — used only to
# decide whether a recovery suggestion is worth offering, never to
# fabricate a specific fact.
_NOT_FOUND_MARKERS = (
    "couldn't find",
    "could not find",
    "couldn't match",
    "could not match",
    "no course found",
    "no assignment found",
    "not found",
    "no results",
    "no documents are indexed",
    "no assignments are tracked",
)


def _short_course_label(name: str | None) -> str:
    if not name:
        return "this course"
    return name.split("-")[0].strip() or name


def generate_followups(
    *,
    user_message: str,
    reply_text: str,
    assignments: list[AssignmentOut],
    courses: list[CourseOut],
    documents: list[DocumentOut],
    used_mcp: bool = False,
    max_followups: int = MAX_FOLLOWUPS,
) -> list[str]:
    """Builds 2-4 real, non-duplicate follow-up prompts from what this turn
    actually returned. Returns `[]` when there's nothing contextual to
    suggest and the reply doesn't look like a "not found" case — an empty
    list is the honest answer, not a bug to paper over with filler."""
    suggestions: list[str] = []
    seen = {user_message.strip().lower()}

    def add(text: str) -> None:
        norm = text.strip().lower()
        if not norm or norm in seen:
            return
        seen.add(norm)
        suggestions.append(text.strip())

    if assignments:
        top = assignments[0]
        label = _short_course_label(top.courseShortName or top.courseName)
        add(f"Tell me more about {label}")
        if not documents:
            add(f"What documents are indexed for {label}?")

    if courses:
        course = courses[0]
        label = course.shortName or course.name
        if not documents:
            add(f"What documents are indexed for {label}?")
        if not assignments:
            add(f"What assignments are due for {label}?")

    if documents and not courses and not assignments:
        doc = documents[0]
        if doc.courseName:
            add(f"What else is indexed for {_short_course_label(doc.courseName)}?")

    if used_mcp:
        add("Ask me another aggregate question about your courses")

    reply_lower = reply_text.lower()
    if any(marker in reply_lower for marker in _NOT_FOUND_MARKERS):
        add("What's due this week?")
        add("What courses do you have?")
        add("What can you do?")

    return suggestions[:max_followups]
