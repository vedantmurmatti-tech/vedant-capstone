"""assignment-action-planner.

A deterministic planning component used by the chat agent's "act" phase
(see chat_agent.py) whenever a message concerns assignments or deadlines.

No Claude Code Skill by this name exists in this repository (checked
`.claude/skills/` and the plugin catalog available to this session) or in
the wider filesystem, so this implements the described responsibility
directly in the backend: given a set of real `Assignment` rows, rank them
by genuine urgency and produce a short, structured action plan. It is pure
rule-based reasoning over database rows — no model call, nothing invented.

Urgency thresholds intentionally match `getAssignmentUrgency` in
frontend/src/lib/utils.ts (overdue / due within 48h / later) so the plan
this produces agrees with what the UI already shows for the same data.
"""

from dataclasses import dataclass
from datetime import datetime

from storage.models import Assignment

Urgency = str  # "overdue" | "due-soon" | "upcoming" | "unscheduled"

_URGENCY_ORDER: dict[Urgency, int] = {"overdue": 0, "due-soon": 1, "upcoming": 2, "unscheduled": 3}

_RECOMMENDATION: dict[Urgency, str] = {
    "overdue": "top priority — already past due",
    "due-soon": "start now if you haven't — due within 48 hours",
    "upcoming": "on track — no action needed yet",
    "unscheduled": "no due date recorded — confirm on the course page",
}


@dataclass
class AssignmentAction:
    assignment: Assignment
    urgency: Urgency
    recommendation: str


def classify_urgency(due_date: datetime | None, *, now: datetime) -> Urgency:
    if due_date is None:
        return "unscheduled"
    hours_remaining = (due_date - now).total_seconds() / 3600
    if hours_remaining < 0:
        return "overdue"
    if hours_remaining <= 48:
        return "due-soon"
    return "upcoming"


def plan_actions(assignments: list[Assignment], *, now: datetime | None = None) -> list[AssignmentAction]:
    """Rank assignments by urgency and attach a plain-language recommendation."""
    now = now or datetime.now()
    actions = [
        AssignmentAction(
            assignment=a,
            urgency=(urgency := classify_urgency(a.due_date, now=now)),
            recommendation=_RECOMMENDATION[urgency],
        )
        for a in assignments
    ]
    actions.sort(key=lambda item: (_URGENCY_ORDER[item.urgency], item.assignment.due_date or datetime.max))
    return actions


def summarize_plan(actions: list[AssignmentAction], *, limit: int = 5) -> str:
    if not actions:
        return "No assignments are currently tracked."

    lines = []
    for i, item in enumerate(actions[:limit], start=1):
        due = item.assignment.due_date.strftime("%b %d, %I:%M %p") if item.assignment.due_date else "no due date"
        lines.append(f"{i}. **{item.assignment.name}** — {due} — {item.recommendation}")

    if len(actions) > limit:
        lines.append(f"…and {len(actions) - limit} more.")

    return "\n".join(lines)
