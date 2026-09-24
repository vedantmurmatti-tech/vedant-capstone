"""Parses/validates the reasoning layer's response-mode decision.

The model (Groq or Gemini — see groq_agent.py/gemini_agent.py, both of
which import this same module rather than duplicating this logic) is
instructed, via one small addition to its existing system prompt, to
append a single trailing marker line to its own reply:

    [ESMERELDA_MODE:VOICE]
or
    [ESMERELDA_MODE:VISUAL]

This module is the one place that marker is parsed, validated, and
stripped — the model's raw text is never trusted or shown to the user
as-is. Anything other than an exact, well-formed VOICE/VISUAL marker
(missing, garbled, a different word, multiple markers, anything) falls
back to "voice" — never a hard failure, never a route, never anything
resembling an instruction to execute. This is deliberately NOT a
frontend keyword list: the decision is made by the same reasoning-layer
call that already has the full conversation history (see Phase 4).
"""

import re

_MARKER_RE = re.compile(r"\n?\[ESMERELDA_MODE:\s*(VOICE|VISUAL)\s*\]\s*$", re.IGNORECASE)


def extract_response_mode(reply_text: str) -> tuple[str, str]:
    """Returns (clean_reply, response_mode). `response_mode` is always
    exactly `"voice"` or `"visual"` — never anything else, regardless of
    what the model actually produced."""
    match = _MARKER_RE.search(reply_text)
    if not match:
        return reply_text, "voice"

    mode = match.group(1).strip().lower()
    clean_reply = reply_text[: match.start()].rstrip()
    if mode not in ("voice", "visual"):
        return clean_reply, "voice"  # unreachable given the regex's own alternation, kept as an explicit safety net
    return clean_reply, mode


def build_visual_context(assignments: list, courses: list, documents: list) -> dict | None:
    """A minimal, structured, non-executable summary of what real,
    already tool-collected data (never fabricated by this function) is
    available for the Conversations view to show — never populated for
    "voice" mode, and never containing anything beyond these three plain
    booleans (no routes, no instructions, no arbitrary model output)."""
    if not (assignments or courses or documents):
        return None
    return {
        "hasAssignments": bool(assignments),
        "hasCourses": bool(courses),
        "hasDocuments": bool(documents),
    }


RESPONSE_MODE_SYSTEM_PROMPT_ADDITION = """
Response mode:
After your normal reply, on its own final line, append exactly one of:
[ESMERELDA_MODE:VOICE]
[ESMERELDA_MODE:VISUAL]

Use VISUAL whenever the user's message is a request to see/open/show/view
something, however it's phrased. This is a near-mechanical rule, not a
judgment call: if the verb is show/open/see/view/look at, it is VISUAL, full
stop — even if what follows is just "those"/"that"/"it"/"the details", and
even if answering it would otherwise be pure recited data with no tool call
of its own. Concrete examples that are ALL VISUAL: "show me those", "show me
the details", "show me the submission details", "let me see the resources",
"open that", "show me the document", "pull up the assignment", "can I see
that". Do not downgrade one of these to VOICE just because you could recite
the same information in words instead — the user asked to SEE it, not hear
it described.
Use VOICE for every other factual question, explanation, or follow-up, even
when it's about the same Moodle data (e.g. "what's due this week", "which one
is for X", "when is that due", "explain this assignment"). Having
assignment/course/document data in your answer does NOT by itself mean
VISUAL — only the user actually asking to see/open/show/view something does.
This marker line is stripped before the user ever sees your reply — never
explain it, mention it, or discuss it in the reply itself.
"""
