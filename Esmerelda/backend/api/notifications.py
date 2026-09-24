"""Proactive academic notifications — the small, reusable abstraction Batch
2 asked for, so Dashboard.tsx never hardcodes any notification text itself.

Every notification here is derived from real, already-stored data this
project's existing systems already collect:
  - "due_soon"     — Assignment.due_date, already synced by
                      moodle/sync_service.py and already exposed via the
                      existing /api/assignments endpoint. Nothing new is
                      scraped or computed beyond a date-range filter.
  - "new_content"  — a real, honest count comparison between the two most
                      recent successful SyncRun rows' own already-stored
                      courses_synced/assignments_synced/resources_synced
                      totals (storage/crud.py's
                      get_last_two_successful_sync_runs()). This is
                      deliberately an aggregate "N more items exist than
                      last time" signal, not a per-item "resource X was
                      added to course Y" — that would need either a
                      first-seen-at column on Resource/Assignment (a real
                      schema change, out of scope for this batch) or
                      reusing content_radar.py's snapshot-diff pattern
                      against live data (a larger change than this batch's
                      "smallest clean mechanism" instruction calls for).
                      See BUILD_LOG.md for this explicitly, as a stated
                      limitation, not something silently simplified away.
  - "sync_error"   — the existing SyncRun.status == "error" row, already
                      surfaced via /api/sync-status; repeated here only so
                      a single `notifications` list can drive one small,
                      consistent UI element instead of two.

The standalone content_radar.py/submission_radar.py scripts (Playwright
prototypes requiring an interactive human login — see moodle/sync_service.py's
own module docstring) are NOT used here — they are not wired into this
backend's automated pipeline at all. The real, live source of this data is
the same database moodle/sync_service.py's run_sync() already populates.

Nothing here ever fabricates a deadline, course, assignment, or resource —
every message is built directly from a real row already in the database,
or is the literal count of one.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from storage.crud import get_last_two_successful_sync_runs
from storage.models import Assignment, Course, SyncRun

from .schemas import ProactiveNotificationOut

# How far ahead an assignment's due date counts as "due soon" for a
# proactive notice — deliberately the same order of magnitude as the
# existing urgency bucketing the frontend already uses elsewhere
# (frontend/src/lib/utils.ts's getAssignmentUrgency "due-soon" = 48h).
_DUE_SOON_WINDOW = timedelta(hours=48)


def _due_soon_notifications(db: Session, user_id: int) -> list[ProactiveNotificationOut]:
    now = datetime.utcnow()
    cutoff = now + _DUE_SOON_WINDOW

    rows = db.execute(
        select(Assignment, Course)
        .join(Course, Assignment.course_id == Course.id)
        .where(Assignment.user_id == user_id)
        .where(Assignment.due_date.is_not(None))
        .where(Assignment.due_date >= now)
        .where(Assignment.due_date <= cutoff)
        .order_by(Assignment.due_date.asc())
    ).all()

    notifications: list[ProactiveNotificationOut] = []
    for assignment, course in rows:
        hours_left = (assignment.due_date - now).total_seconds() / 3600
        when = "today" if hours_left <= 24 else "tomorrow" if hours_left <= 48 else None
        if when is None:
            continue  # outside the window in practice — defensive, shouldn't happen given the query above
        notifications.append(
            ProactiveNotificationOut(
                id=f"due-{assignment.id}",
                kind="due_soon",
                message=f'"{assignment.name}" ({course.short_name or course.name}) is due {when}.',
                courseId=course.id,
                assignmentId=assignment.id,
            )
        )
    return notifications


def compute_new_items_count(user_id: int) -> int | None:
    """None when there's no earlier successful sync to compare against yet
    (genuinely unknown — never reported as 0). Otherwise the sum of any
    positive growth in courses/assignments/resources between the two most
    recent successful syncs' own already-stored counts — the one place
    this delta is computed; both the dashboard summary's `newItemsCount`
    and this module's own "new_content" notification call this."""
    runs = get_last_two_successful_sync_runs(user_id)
    if len(runs) < 2:
        return None
    latest, previous = runs[0], runs[1]
    return (
        max(0, latest.courses_synced - previous.courses_synced)
        + max(0, latest.assignments_synced - previous.assignments_synced)
        + max(0, latest.resources_synced - previous.resources_synced)
    )


def _new_content_notification(db: Session, user_id: int) -> ProactiveNotificationOut | None:
    runs = get_last_two_successful_sync_runs(user_id)
    if len(runs) < 2:
        return None  # no earlier successful sync to compare against — genuinely unknown, not "zero"
    delta = compute_new_items_count(user_id)
    if not delta:
        return None

    noun = "item" if delta == 1 else "items"
    return ProactiveNotificationOut(
        id=f"new-content-{runs[0].id}",
        kind="new_content",
        message=f"{delta} new {noun} found since your last sync.",
    )


def _sync_error_notification(db: Session, user_id: int) -> ProactiveNotificationOut | None:
    latest_run = db.scalar(
        select(SyncRun).where(SyncRun.user_id == user_id).order_by(SyncRun.id.desc())
    )
    if latest_run is None or latest_run.status != "error":
        return None
    return ProactiveNotificationOut(
        id=f"sync-error-{latest_run.id}",
        kind="sync_error",
        message="Your last Moodle sync failed — some data may be out of date.",
    )


def build_proactive_notifications(db: Session, user_id: int) -> list[ProactiveNotificationOut]:
    """The one entry point this module exposes — called from
    api/routes.py's dashboard-summary endpoint. Reuses only real,
    already-stored data; never triggers a sync or scrapes anything itself."""
    notifications = _due_soon_notifications(db, user_id)
    new_content = _new_content_notification(db, user_id)
    if new_content:
        notifications.append(new_content)
    sync_error = _sync_error_notification(db, user_id)
    if sync_error:
        notifications.append(sync_error)
    return notifications
