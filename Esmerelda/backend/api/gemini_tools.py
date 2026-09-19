"""Tool functions exposed to Gemini's automatic function-calling.

Each function here is real, callable Python bound to a live DB session via
closures — Gemini calls these directly (the `google-genai` SDK executes
them and feeds the return value back to the model); there is no separate
"fake" implementation. Every one of them only ever returns what queries.py
actually fetched from storage/esmerelda.db, so the model has nothing to
fabricate from — if nothing matches, the tool says so explicitly.

`ToolCollector` captures the real response-model objects (`AssignmentOut` /
`CourseOut` / `DocumentOut`) each call fetched, so the FastAPI route can
still return them to the frontend as structured chips — the same contract
`deterministic_agent.py` already produces — instead of the frontend only
ever seeing Gemini's prose.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from . import document_retrieval, queries
from .matching import match_assignment, match_course
from .schemas import AssignmentOut, ChatSourceOut, CourseOut, DocumentOut
from .skills.assignment_action_planner import plan_actions, plan_for_assignment

logger = logging.getLogger("esmerelda.chat")

MAX_RESULTS = 8


@dataclass
class ToolCollector:
    assignments: dict[int, AssignmentOut] = field(default_factory=dict)
    courses: dict[int, CourseOut] = field(default_factory=dict)
    documents: dict[int, DocumentOut] = field(default_factory=dict)
    sources: list[ChatSourceOut] = field(default_factory=list)


def build_tools(db: Session, collector: ToolCollector) -> list[Callable]:
    def get_upcoming_assignments() -> dict:
        """Look up the student's real tracked assignments across every course, ranked by real urgency (overdue, due soon, or upcoming). Use this for any question about deadlines, what's due, or priorities. Takes no arguments."""
        rows = queries.fetch_all_assignments(db)
        if not rows:
            return {"assignments": [], "note": "No assignments are currently tracked in the database."}

        course_by_id = {a.id: c for a, c in rows}
        actions = plan_actions([a for a, _c in rows])
        collector.sources.append(ChatSourceOut(label="Assignment Timeline", courseName="All courses"))

        results = []
        for action in actions[:MAX_RESULTS]:
            course = course_by_id[action.assignment.id]
            out = queries.assignment_out(action.assignment, course)
            collector.assignments[out.id] = out
            results.append(
                {
                    "name": action.assignment.name,
                    "course": course.name,
                    "due_date": action.assignment.due_date.isoformat() if action.assignment.due_date else None,
                    "urgency": action.urgency,
                    "recommendation": action.recommendation,
                }
            )
        return {"assignments": results}

    def get_course_info(course_query: str) -> dict:
        """Look up real information about one specific course by name or course code (e.g. "Tangible Interfaces" or "DESG322"). Returns the course's full name, short code, description, and how many assignments/resources are tracked for it — COUNTS only, not the assignments themselves. Use get_course_assignments instead when the user wants the actual list of assignments/due dates for a course. Args: course_query: the course name or code as mentioned by the user."""
        courses = queries.fetch_courses(db)
        course = match_course(course_query, courses)
        if course is None:
            return {
                "error": f"No course found matching '{course_query}'.",
                "available_courses": [c.name for c in courses],
            }

        out = queries.course_out(course)
        collector.courses[out.id] = out
        assignments = queries.fetch_course_assignments(db, course.id)
        resources = queries.fetch_course_resources(db, course.id)
        collector.sources.append(ChatSourceOut(label="Course Catalog", courseName=course.name))

        return {
            "name": course.name,
            "short_name": out.shortName,
            "description": course.description,
            "tracked_assignment_count": len(assignments),
            "tracked_resource_count": len(resources),
        }

    def get_course_assignments(course_query: str) -> dict:
        """Look up the REAL tracked assignments for ONE specific course — with their real due dates, urgency, and submission status. Use this — not get_course_info, which only returns a count — whenever the user asks what's due, or what assignments exist, for a named or specific course (e.g. "what are my assignments for DESG319-UGSEM5-2026/27S1-Introduction to Artificial Intelligence & Machine Learning" or "what's due in Tangible Interfaces"). The course is resolved deterministically from the real database (by Moodle course id, short name, course code, or full name — tolerant of a truncated or partial course name) — never guess a course's assignments from get_upcoming_assignments' cross-course list, which only shows the 8 most urgent overall and may omit a specific course entirely. Args: course_query: the course name, short code, or Moodle course id, exactly as the user mentioned it."""
        courses = queries.fetch_courses(db)
        course = match_course(course_query, courses)
        if course is None:
            return {
                "error": f"No course found matching '{course_query}'.",
                "available_courses": [c.name for c in courses],
            }

        collector.courses[course.id] = queries.course_out(course)
        assignments = queries.fetch_course_assignments(db, course.id)
        if not assignments:
            return {
                "course": course.name,
                "assignments": [],
                "note": "No assignments are currently tracked for this course in the database.",
            }

        actions = plan_actions(assignments)
        collector.sources.append(ChatSourceOut(label="Assignment Timeline", courseName=course.name))

        results = []
        for action in actions:
            out = queries.assignment_out(action.assignment, course)
            collector.assignments[out.id] = out
            results.append(
                {
                    "name": action.assignment.name,
                    "due_date": action.assignment.due_date.isoformat() if action.assignment.due_date else None,
                    "urgency": action.urgency,
                    "recommendation": action.recommendation,
                    "submission_status": action.assignment.submission_status,
                }
            )
        return {"course": course.name, "assignments": results}

    def get_course_documents(course_query: str) -> dict:
        """Look up real indexed documents (files downloaded from Moodle) for a specific course, or pass an empty string to get the most recently indexed documents across all courses. Args: course_query: the course name or code, or "" for all courses."""
        courses = queries.fetch_courses(db)
        matched = match_course(course_query, courses) if course_query.strip() else None

        if course_query.strip() and matched is None:
            return {
                "error": f"No course found matching '{course_query}'.",
                "available_courses": [c.name for c in courses],
            }

        rows = queries.fetch_all_documents(db)
        if matched:
            rows = [row for row in rows if row[2] and row[2].id == matched.id]
        rows = rows[:MAX_RESULTS]

        if not rows:
            return {"documents": [], "note": "No documents are indexed for that scope yet."}

        collector.sources.append(
            ChatSourceOut(label="Knowledge Base", courseName=matched.name if matched else "All courses")
        )

        results = []
        for doc, _resource, course, updated_at in rows:
            out = queries.document_out(doc, course, updated_at)
            collector.documents[out.id] = out
            results.append(
                {
                    "name": doc.name,
                    "course": course.name if course else "Unassigned",
                    "file_type": out.fileType,
                    "updated_at": updated_at.isoformat() if updated_at else None,
                }
            )
        return {"documents": results}

    def search_document_content(query: str) -> dict:
        """Search the ACTUAL text content of real, downloaded Moodle documents (PDFs, DOCX, PPTX) for a specific question or topic — e.g. "what does the Service Design project brief say about requirements" or "summarize the TRENDS Matrix document". Use this whenever the user asks what a document says, requires, or covers — get_course_documents only returns document names/metadata, never their content. Returns the actual matching excerpts, quoted from the real files, with which document/course each came from. Args: query: the question or topic to search for, in the user's own words."""
        chunks = document_retrieval.search_documents(db, query)
        retrieved_names = [c.document_name for c in chunks]
        logger.info("[CHAT] retrieved documents=%s", retrieved_names)
        logger.info("[CHAT] retrieved chunks=%d", len(chunks))

        if not chunks:
            indexed_count = document_retrieval.count_indexed_documents(db)
            return {
                "excerpts": [],
                "note": (
                    "No indexed document content matched that query."
                    if indexed_count > 0
                    else "No documents have been indexed with searchable text yet."
                ),
            }

        collector.sources.append(ChatSourceOut(label="Document Content", courseName="Knowledge Base"))
        context_chars = sum(len(c.chunk_text) for c in chunks)
        logger.info("[CHAT] context chars=%d", context_chars)

        return {
            "excerpts": [
                {
                    "document": c.document_name,
                    "course": c.course_name or "Unassigned",
                    "text": c.chunk_text,
                }
                for c in chunks
            ]
        }

    def plan_assignment_action(assignment_query: str) -> dict:
        """Run the assignment-action-planner Skill on ONE specific real assignment named or described by the user (e.g. "the MVP assignment" or "Assessment 2"). Use this — not get_upcoming_assignments — when the user asks what to do about a single named assignment. Breaks the assignment into its explicit requirements and returns a status (completed/incomplete/unverified), a concrete next action, an expected deliverable, how to verify it, and real evidence for each one — plus the real submission link/Moodle IDs and anything Esmerelda couldn't determine. Args: assignment_query: the assignment name or a distinctive phrase from it, as the user mentioned it."""
        rows = queries.fetch_all_assignments(db)
        assignments = [a for a, _c in rows]
        course_by_id = {a.id: c for a, c in rows}

        matched = match_assignment(assignment_query, assignments)
        if matched is None:
            return {
                "error": f"No assignment found matching '{assignment_query}'.",
                "available_assignments": [a.name for a in assignments],
            }

        course = course_by_id[matched.id]
        matched_resource = queries.fetch_resource_by_moodle_id(db, matched.moodle_id)
        related_documents = (
            queries.fetch_documents_for_resource(db, matched_resource.id) if matched_resource else []
        )
        plan = plan_for_assignment(
            matched, course, matched_resource=matched_resource, related_documents=related_documents
        )

        collector.assignments[matched.id] = queries.assignment_out(matched, course)
        collector.sources.append(
            ChatSourceOut(label="assignment-action-planner Skill", courseName=course.name)
        )

        return {
            "assignment_name": plan.assignment_name,
            "course_name": plan.course_name,
            "due_date": plan.due_date,
            "urgency": plan.urgency,
            "requirement_source": plan.requirement_source,
            "requirements": [
                {
                    "requirement": r.requirement,
                    "status": r.status,
                    "next_action": r.next_action,
                    "expected_deliverable": r.expected_deliverable,
                    "verification_method": r.verification_method,
                    "evidence": r.evidence,
                }
                for r in plan.requirements
            ],
            "missing_information": [
                {"item": m.item, "why_it_matters": m.why_it_matters} for m in plan.missing_information
            ],
            "grounding": {
                "submission_url": plan.grounding.submission_url,
                "moodle_assignment_id": plan.grounding.moodle_assignment_id,
                "moodle_course_id": plan.grounding.moodle_course_id,
            },
        }

    return [
        get_upcoming_assignments,
        get_course_info,
        get_course_assignments,
        get_course_documents,
        search_document_content,
        plan_assignment_action,
    ]
