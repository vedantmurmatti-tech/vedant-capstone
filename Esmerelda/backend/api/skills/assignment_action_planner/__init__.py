"""assignment-action-planner Skill.

See SKILL.md in this directory for the full spec (scope, input, process,
output, limitations). Summary: deterministic, rule-based reasoning over
real `Assignment`/`Course`/`Resource`/`Document` rows and two bounded,
real local-evidence sources (`BUILD_LOG.md`, `backend/api/` source file
names) — no LLM call anywhere in this module.

Entrypoints:
  plan_for_assignment() — the Skill proper: one real assignment (+ its
                           course, and optionally its matched resource and
                           any documents downloaded for it) in, one
                           requirement-by-requirement, source-grounded
                           AssignmentActionPlan out.
  plan_actions()        — batch mode: unchanged from the original
                           implementation. Ranks a list of assignments by
                           urgency for the "what's due this week" case —
                           a different, coarser-grained use case that this
                           redesign deliberately leaves alone.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from storage.models import Assignment, Course, Document, Resource

Urgency = str  # "overdue" | "due-soon" | "upcoming" | "unscheduled"
Status = Literal["completed", "incomplete", "unverified"]

_URGENCY_ORDER: dict[Urgency, int] = {"overdue": 0, "due-soon": 1, "upcoming": 2, "unscheduled": 3}

_RECOMMENDATION: dict[Urgency, str] = {
    "overdue": "top priority — already past due",
    "due-soon": "start now if you haven't — due within 48 hours",
    "upcoming": "on track — no action needed yet",
    "unscheduled": "no due date recorded — confirm on the course page",
}


def _find_repo_root(start: Path) -> Path:
    """Locates the repo root that holds BUILD_LOG.md, without assuming a
    fixed directory depth.

    Locally, `start` (this file's containing directory) is 5 levels below
    the repo root (backend/api/skills/assignment_action_planner/ ->
    backend/api/skills/ -> backend/api/ -> backend/ -> Esmerelda/ ->
    <repo root>), which a previous version hardcoded as
    `Path(__file__).resolve().parents[5]`. Inside the Docker image, only
    backend/'s own contents are copied to /app (see backend/Dockerfile) —
    BUILD_LOG.md doesn't exist there at all, and /app is only 4 levels
    below the filesystem root, so `parents[5]` raised `IndexError: 5` and
    crashed the whole module at import time.

    `ESMERELDA_REPO_ROOT` can override this explicitly if ever needed.
    Otherwise this walks upward from `start` looking for BUILD_LOG.md,
    and falls back to `start` itself if it's never found (e.g. inside
    the container) — search_local_evidence() below already treats a
    missing BUILD_LOG.md as "no evidence found", not an error, so a
    fallback path that simply doesn't exist is safe.
    """
    override = os.environ.get("ESMERELDA_REPO_ROOT")
    if override:
        return Path(override)
    for candidate in (start, *start.parents):
        if (candidate / "BUILD_LOG.md").is_file():
            return candidate
    return start


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)
_BUILD_LOG_PATH = _REPO_ROOT / "BUILD_LOG.md"
_API_SOURCE_DIR = Path(__file__).resolve().parents[2]  # backend/api/

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "with", "into", "one", "two",
    "to", "in", "on", "your", "this", "that", "it", "is", "are",
}


def classify_urgency(due_date: datetime | None, *, now: datetime) -> Urgency:
    if due_date is None:
        return "unscheduled"
    hours_remaining = (due_date - now).total_seconds() / 3600
    if hours_remaining < 0:
        return "overdue"
    if hours_remaining <= 48:
        return "due-soon"
    return "upcoming"


# --- requirement extraction --------------------------------------------------

_TITLE_PREFIX_RE = re.compile(r"^(assessment|assignment)\s*\d+\s*[:\-]\s*", re.IGNORECASE)


def extract_requirements(assignment: Assignment) -> tuple[list[str], str]:
    """Breaks an assignment into explicit requirement phrases, using real
    text only — never invented. Prefers `assignment.description` when the
    Moodle sync actually captured one (it doesn't for any assignment in
    the current dataset — see SKILL.md); falls back to the assignment's
    own title with the "Assessment N:" prefix stripped. Returns
    (phrases, source_label) so the caller/output always states which real
    field the requirements came from."""
    if assignment.description and assignment.description.strip():
        raw, source = assignment.description.strip(), "assignment.description"
    else:
        raw, source = assignment.name, "assignment.name (no description synced — title used instead)"

    text = _TITLE_PREFIX_RE.sub("", raw).strip()
    parts = re.split(r",|\band\b", text, flags=re.IGNORECASE)
    phrases = [p.strip().rstrip(".") for p in parts if p.strip()]
    return (phrases or [text]), source


def _significant_words(phrase: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]+", phrase.lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


# --- local evidence search ---------------------------------------------------


@dataclass
class EvidenceMatch:
    description: str
    location: str
    excerpt: str | None = None


def search_local_evidence(
    requirement: str,
    *,
    build_log_path: Path = _BUILD_LOG_PATH,
    api_dir: Path = _API_SOURCE_DIR,
    max_matches: int = 3,
) -> list[EvidenceMatch]:
    """Bounded, real, generalizable evidence search — not specific to any
    one assignment. Searches exactly two real, fixed locations for a
    case-insensitive keyword match against the requirement's significant
    words: this project's own BUILD_LOG.md (line by line, real excerpts
    quoted verbatim) and the filenames under backend/api/ (existence
    only). Returns [] the moment nothing real is found — never fabricates
    a match. This is a heuristic keyword/filename check, not a claim that
    the matched code is correct or complete; SKILL.md and every plan's
    verification_method field say so explicitly."""
    words = _significant_words(requirement)
    if not words:
        return []

    matches: list[EvidenceMatch] = []

    if build_log_path.is_file():
        text = build_log_path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            lower = line.lower()
            # Require every significant word from the requirement phrase to appear in
            # the same line — an OR match (e.g. "skill" alone) is too loose and pulls
            # in unrelated lines (an earlier version matched "Custom Skill" against a
            # line about ChatMarkdown just because it contained the word "custom").
            if all(w in lower for w in words) and line.strip().startswith(("#", "-", "*")):
                matches.append(
                    EvidenceMatch(
                        description=f"Mentioned in {build_log_path.name}",
                        location=build_log_path.name,
                        excerpt=line.strip()[:220],
                    )
                )
                if len(matches) >= max_matches:
                    return matches

    if api_dir.is_dir():
        for path in sorted(api_dir.rglob("*.py")):
            if any(w in path.stem.lower() for w in words):
                matches.append(
                    EvidenceMatch(
                        description="Matching source file found",
                        location=str(path.relative_to(api_dir.parent)),
                    )
                )
                if len(matches) >= max_matches:
                    break

    return matches


# --- single-assignment Skill contract ---------------------------------------


@dataclass
class Grounding:
    submission_url: str | None
    moodle_assignment_id: str
    moodle_course_id: str


@dataclass
class RequirementPlan:
    requirement: str
    status: Status
    next_action: str
    expected_deliverable: str
    verification_method: str
    evidence: str


@dataclass
class MissingInformation:
    item: str
    why_it_matters: str


@dataclass
class AssignmentActionPlan:
    assignment_name: str
    course_name: str
    due_date: str | None
    urgency: Urgency
    requirement_source: str
    requirements: list[RequirementPlan] = field(default_factory=list)
    missing_information: list[MissingInformation] = field(default_factory=list)
    grounding: Grounding = None  # type: ignore[assignment]


def _plan_one_requirement(
    requirement: str,
    *,
    matched_resource: Resource | None,
    related_documents: list[Document],
) -> RequirementPlan:
    local_evidence = search_local_evidence(requirement)

    if local_evidence:
        citations = []
        file_hits = [m for m in local_evidence if m.excerpt is None]
        log_hits = [m for m in local_evidence if m.excerpt is not None]
        if file_hits:
            citations.append("file(s): " + ", ".join(m.location for m in file_hits))
        if log_hits:
            citations.append(f'{log_hits[0].location}: "{log_hits[0].excerpt}"')
        return RequirementPlan(
            requirement=requirement,
            status="completed",
            next_action=(
                f"Re-confirm '{requirement}' still works, then reference this evidence in your submission notes."
            ),
            expected_deliverable=f"A working, demonstrable implementation of: {requirement}",
            verification_method="Re-run the cited file/test locally and re-read the cited BUILD_LOG.md entry.",
            evidence="; ".join(citations),
        )

    if matched_resource and not related_documents:
        return RequirementPlan(
            requirement=requirement,
            status="incomplete",
            next_action=(
                f"Open the matched Moodle resource for this assignment and gather what it says "
                f"about '{requirement}', then implement it."
            ),
            expected_deliverable=f"A working implementation of: {requirement}",
            verification_method=f"Review the resource directly: {matched_resource.url or '(no URL recorded)'}",
            evidence=f"Moodle resource matched ('{matched_resource.name}') but no document has been downloaded for it yet.",
        )

    return RequirementPlan(
        requirement=requirement,
        status="unverified",
        next_action=(
            f"Manually check whether '{requirement}' is done — Esmerelda found no local project evidence "
            "or synced Moodle document for it either way."
        ),
        expected_deliverable=f"Clear evidence (code, a document, or notes) that '{requirement}' is satisfied",
        verification_method="Check your own repository/notes, or confirm directly on the Moodle assignment page.",
        evidence="No match in BUILD_LOG.md, backend/api/ source files, or synced Moodle documents.",
    )


def plan_for_assignment(
    assignment: Assignment,
    course: Course,
    *,
    now: datetime | None = None,
    matched_resource: Resource | None = None,
    related_documents: list[Document] | None = None,
) -> AssignmentActionPlan:
    """The Skill. `matched_resource`/`related_documents` are real ORM rows
    the *caller* already fetched (see queries.fetch_resource_by_moodle_id /
    fetch_documents_for_resource) — this function never queries the
    database itself and never talks to MCP; it only reasons over what it's
    given, plus the two bounded local-evidence sources above."""
    now = now or datetime.now()
    related_documents = related_documents or []
    urgency = classify_urgency(assignment.due_date, now=now)
    requirement_phrases, requirement_source = extract_requirements(assignment)

    requirements = [
        _plan_one_requirement(phrase, matched_resource=matched_resource, related_documents=related_documents)
        for phrase in requirement_phrases
    ]

    missing_information: list[MissingInformation] = []
    if not assignment.description or not assignment.description.strip():
        missing_information.append(
            MissingInformation(
                item="Assignment description text",
                why_it_matters=(
                    "Requirements were inferred from the assignment title only — the real Moodle "
                    "description (if any) hasn't been synced, so this may be incomplete."
                ),
            )
        )
    if matched_resource is None:
        missing_information.append(
            MissingInformation(
                item="A matching Moodle resource entry for this assignment",
                why_it_matters="Without it, Esmerelda can't point you to the assignment's own Moodle page content.",
            )
        )
    elif not related_documents:
        missing_information.append(
            MissingInformation(
                item=f"A downloaded document for '{matched_resource.name}'",
                why_it_matters="No file has been synced for this assignment yet, so its real content couldn't be checked.",
            )
        )

    return AssignmentActionPlan(
        assignment_name=assignment.name,
        course_name=course.name,
        due_date=assignment.due_date.isoformat() if assignment.due_date else None,
        urgency=urgency,
        requirement_source=requirement_source,
        requirements=requirements,
        missing_information=missing_information,
        grounding=Grounding(
            submission_url=assignment.submission_url,
            moodle_assignment_id=assignment.moodle_id,
            moodle_course_id=course.moodle_id,
        ),
    )


def render_plan(plan: AssignmentActionPlan) -> str:
    due = "no due date recorded"
    if plan.due_date:
        due = datetime.fromisoformat(plan.due_date).strftime("%b %d, %I:%M %p")

    lines = [
        f"**{plan.assignment_name}** ({plan.course_name})",
        f"Due: {due} · Urgency: {plan.urgency}",
        f"Requirements extracted from: {plan.requirement_source}",
        "",
    ]

    status_label = {"completed": "[Completed]", "incomplete": "[Incomplete]", "unverified": "[Unverified]"}
    for i, req in enumerate(plan.requirements, start=1):
        lines.append(f"{i}. **{req.requirement}** — {status_label[req.status]}")
        lines.append(f"   - Next action: {req.next_action}")
        lines.append(f"   - Expected deliverable: {req.expected_deliverable}")
        lines.append(f"   - Verify by: {req.verification_method}")
        lines.append(f"   - Evidence: {req.evidence}")

    if plan.missing_information:
        lines.append("")
        lines.append("Missing information:")
        for m in plan.missing_information:
            lines.append(f"- {m.item} — {m.why_it_matters}")

    lines.append("")
    if plan.grounding.submission_url:
        lines.append(f"Source: {plan.grounding.submission_url}")
    else:
        lines.append("Source: no submission link recorded for this assignment")

    return "\n".join(lines)


# --- batch mode (unchanged from the pre-Skill implementation) --------------


@dataclass
class AssignmentAction:
    assignment: Assignment
    urgency: Urgency
    recommendation: str


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
