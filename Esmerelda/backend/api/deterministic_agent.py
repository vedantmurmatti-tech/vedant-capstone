"""Esmerelda's deterministic fallback agent: perceive -> reason -> act -> observe -> iterate.

This was the primary chat agent before Gemini function-calling was wired up
(see gemini_agent.py, now the primary path via chat_agent.py's orchestrator).
It's kept as-is and used as a fallback whenever Gemini is unreachable or
unconfigured, so the chat feature degrades to something real instead of
failing outright. There is no LLM call anywhere in this module. It composes
its reply from real records only; when it can't map a message to anything
it can look up, it says so instead of guessing.

Loop shape, once per incoming message:
  perceive  — read the real course list, detect keyword-based intents and
              (optionally) which real course the message refers to.
  reason    — turn that perception into an ordered, bounded list of steps
              to execute (MAX_STEPS caps it so the loop always terminates).
  act       — each step runs a real query (via queries.py) and, for
              assignment-related steps, the assignment-action-planner
              (see skills/assignment_action_planner/) to rank/recommend.
  observe   — each step appends its findings to a shared AgentContext.
  iterate   — the loop runs every reasoned step in turn (a compound
              question like a course name plus "documents" executes more
              than one act cycle) before composing the final reply.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from storage.models import Course

from . import queries
from .followups import generate_followups
from .matching import match_course
from .skills.assignment_action_planner import plan_actions, summarize_plan
from .schemas import AssignmentOut, ChatResponseOut, ChatSourceOut, CourseOut, DocumentOut

MAX_STEPS = 3

INTENT_KEYWORDS: dict[str, list[str]] = {
    "deadlines": ["due", "deadline", "assignment", "submit", "overdue", "priorit"],
    "documents": ["document", "file", "notes", "slide", "pdf", "resource", "material", "reading"],
    "sync": ["sync", "moodle", "refresh", "last update"],
    "help": ["help", "what can you do", "hello", "hi there", " hi "],
}


@dataclass
class Perception:
    intents: list[str]
    course: Course | None


@dataclass
class AgentContext:
    reply_parts: list[str] = field(default_factory=list)
    assignments: dict[int, AssignmentOut] = field(default_factory=dict)
    courses: dict[int, CourseOut] = field(default_factory=dict)
    documents: dict[int, DocumentOut] = field(default_factory=dict)
    sources: list[ChatSourceOut] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)


# --- perceive ---------------------------------------------------------------


def _perceive(message: str, courses: list[Course]) -> Perception:
    lower = f" {message.lower()} "
    intents = [name for name, keywords in INTENT_KEYWORDS.items() if any(kw in lower for kw in keywords)]
    return Perception(intents=intents or [], course=match_course(message, courses))


# --- reason ------------------------------------------------------------------


def _reason(perception: Perception) -> list[str]:
    if perception.course:
        return ["course_lookup"]

    if perception.intents == ["help"]:
        return ["help"]

    steps = [intent for intent in ("deadlines", "documents", "sync") if intent in perception.intents]
    if not steps:
        steps = ["fallback"]
    return steps[:MAX_STEPS]


# --- act / observe -------------------------------------------------------


def _act_help(ctx: AgentContext) -> None:
    ctx.reply_parts.append(
        "I can help with a few things right now:\n"
        "- **Deadlines** — ask \"what's due this week\" or \"scan my upcoming deadlines\"\n"
        "- **A specific course** — mention it by name, e.g. \"Tangible Interfaces\"\n"
        "- **Documents** — ask \"what documents do you have for [course]\"\n"
        "- **Sync status** — ask \"when did you last sync with Moodle\"\n\n"
        "I only answer from what's actually been synced from Moodle — I won't make anything up."
    )
    ctx.follow_ups.extend(["What's due this week?", "When did you last sync with Moodle?"])


def _act_course_lookup(db: Session, course: Course, perception: Perception, ctx: AgentContext) -> None:
    ctx.courses[course.id] = queries.course_out(course)

    assignments = queries.fetch_course_assignments(db, course.id)
    resources = queries.fetch_course_resources(db, course.id)

    short = course.short_name or queries.course_out(course).shortName or course.name
    parts = [f"**{course.name}** ({short}) — {len(assignments)} assignment(s), {len(resources)} resource(s) tracked."]

    if assignments:
        actions = plan_actions(assignments)
        parts.append(summarize_plan(actions))
        for action in actions[:5]:
            ctx.assignments[action.assignment.id] = queries.assignment_out(action.assignment, course)
        ctx.sources.append(ChatSourceOut(label="Assignment Timeline", courseName=course.name))
    else:
        parts.append("No assignments are tracked for this course yet.")

    # Compound query — e.g. "what documents does Tangible Interfaces have" —
    # matched both a course and the documents intent, so fold both in here
    # rather than reasoning them as two disconnected steps.
    if "documents" in perception.intents:
        course_docs = [row for row in queries.fetch_all_documents(db) if row[2] and row[2].id == course.id][:5]
        if course_docs:
            doc_lines = [f"- **{doc.name}**" for doc, _r, _c, _u in course_docs]
            parts.append("Documents indexed for this course:\n" + "\n".join(doc_lines))
            for doc, _resource, _course, updated_at in course_docs:
                ctx.documents[doc.id] = queries.document_out(doc, course, updated_at)
            ctx.sources.append(ChatSourceOut(label="Knowledge Base", courseName=course.name))
        else:
            parts.append("No documents are indexed for this course yet.")

    ctx.reply_parts.append("\n\n".join(parts))


def _act_deadlines(db: Session, ctx: AgentContext) -> None:
    rows = queries.fetch_all_assignments(db)
    course_by_assignment = {a.id: c for a, c in rows}
    actions = plan_actions([a for a, _c in rows])

    ctx.reply_parts.append("Here's your priority plan, ranked by real urgency:\n\n" + summarize_plan(actions))
    for action in actions[:5]:
        course = course_by_assignment[action.assignment.id]
        ctx.assignments[action.assignment.id] = queries.assignment_out(action.assignment, course)
    if actions:
        ctx.sources.append(ChatSourceOut(label="Assignment Timeline", courseName="All courses"))


def _act_documents(db: Session, ctx: AgentContext) -> None:
    rows = queries.fetch_all_documents(db)[:5]
    if not rows:
        ctx.reply_parts.append("No documents have been indexed from Moodle yet.")
        return

    lines = ["Here are the most recently indexed documents:\n"]
    for doc, _resource, course, updated_at in rows:
        out = queries.document_out(doc, course, updated_at)
        ctx.documents[doc.id] = out
        lines.append(f"- **{doc.name}** ({course.name if course else 'Unassigned'})")
    ctx.reply_parts.append("\n".join(lines))
    ctx.sources.append(ChatSourceOut(label="Knowledge Base", courseName="All courses"))


def _act_sync(db: Session, ctx: AgentContext) -> None:
    status = queries.fetch_sync_status(db)
    if status.lastSyncedAt:
        ctx.reply_parts.append(
            f"Last synced **{status.lastSyncedAt:%b %d, %I:%M %p}**, tracking {status.coursesTracked} course(s)."
        )
    else:
        ctx.reply_parts.append("No sync has completed yet — no document has been downloaded.")


def _act_fallback(ctx: AgentContext) -> None:
    ctx.reply_parts.append(
        "I couldn't match that to your real Moodle data yet. Try asking about a deadline, a specific "
        "course by name, indexed documents, or when Esmerelda last synced."
    )


def _act(step: str, db: Session, perception: Perception, ctx: AgentContext) -> None:
    if step == "help":
        _act_help(ctx)
    elif step == "course_lookup" and perception.course:
        _act_course_lookup(db, perception.course, perception, ctx)
    elif step == "deadlines":
        _act_deadlines(db, ctx)
    elif step == "documents":
        _act_documents(db, ctx)
    elif step == "sync":
        _act_sync(db, ctx)
    else:
        _act_fallback(ctx)


# --- entry point -------------------------------------------------------------


def handle_chat_message_deterministic(db: Session, message: str) -> ChatResponseOut:
    courses = queries.fetch_courses(db)
    perception = _perceive(message, courses)
    steps = _reason(perception)

    ctx = AgentContext()
    for step in steps:
        _act(step, db, perception, ctx)

    reply = "\n\n".join(part for part in ctx.reply_parts if part) or (
        "I don't have anything to say about that yet."
    )

    assignments = list(ctx.assignments.values())
    courses_out = list(ctx.courses.values())
    documents = list(ctx.documents.values())

    generated = generate_followups(
        user_message=message,
        reply_text=reply,
        assignments=assignments,
        courses=courses_out,
        documents=documents,
    )
    # _act_help's capability-discovery pair is curated by hand (it isn't
    # driven by tool results, so the shared generator wouldn't produce it)
    # and takes priority; the generated ones fill in the rest, deduped.
    follow_ups = list(ctx.follow_ups)
    for g in generated:
        if g.lower() not in (f.lower() for f in follow_ups):
            follow_ups.append(g)

    return ChatResponseOut(
        reply=reply,
        assignments=assignments,
        courses=courses_out,
        documents=documents,
        sources=ctx.sources,
        followUps=follow_ups[:4],
    )
