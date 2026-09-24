"""Esmerelda's CURRENT user-identification mechanism — explicitly a
development/local identity boundary, NOT production authentication.

See BUILD_LOG.md's multi-user foundation entry for the full picture. What
this module actually does: reads a plain, unsigned `X-Esmerelda-User-Id`
header (or `?userId=` query param, for contexts a header is awkward in —
e.g. a plain `<a href>` document download link) and resolves it to a real
User row, creating the row the first time a given id/email is seen so a
frontend "switch user" control has something real to point at without a
separate signup step existing yet.

What this deliberately does NOT do, and must not be read as doing:
  - No password, no session cookie, no token, no signature — anyone who
    can reach this API at all can claim to be any user id just by setting
    a header. There is nothing here that verifies the caller actually IS
    that user.
  - No connection whatsoever to Moodle/Google SSO identity — that's a
    separate concern (moodle/browser.py's per-user Playwright profile),
    intentionally decoupled so this module doesn't need real SSO
    integration to exist for the ownership boundary underneath it to be
    real and testable.

Why build this instead of nothing: Part 8 of the task that introduced
this file requires the BACKEND — not the frontend — to enforce that User
A's queries can never return User B's rows, and requires that
enforcement to be real and testable now, before real production
authentication exists. Every route in routes.py depends on
get_current_user_id() below and threads the resulting id through to
storage/crud.py and api/queries.py, which filter every query by it — so
the ownership boundary itself is already the real, final shape production
auth will slot into; only the "how do we know who's asking" step here is
a placeholder.

Replacing this with real authentication (verifying a real login, e.g. via
Moodle Web Service tokens or a proper session/JWT layer once FLAME's
Moodle admin confirms self-service tokens are available — see
BUILD_LOG.md's earlier investigation entries) is real, separate,
un-started work. Do not deploy this application as-is anywhere reachable
by more than one trusted person.
"""

from fastapi import Header, Query

from sqlalchemy import select
from sqlalchemy.orm import Session

from storage.database import SessionLocal
from storage.models import User

from .deps import get_db
from fastapi import Depends

DEFAULT_DEV_USER_EMAIL = "demo@local.esmerelda"


def _get_or_create_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_or_create_demo_user() -> int:
    """Resolves (creating if genuinely absent — should already exist from
    storage/database.py's migration) the one demo user id every unscoped
    dev request falls back to. A plain module-level function, not a
    FastAPI dependency itself, so scripts/tests can call it directly
    without spinning up the app."""
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == DEFAULT_DEV_USER_EMAIL))
        if user is None:
            user = User(email=DEFAULT_DEV_USER_EMAIL, display_name="Local Demo User")
            session.add(user)
            session.commit()
            session.refresh(user)
        return user.id


def get_current_user_id(
    db: Session = Depends(get_db),
    x_esmerelda_user_id: str | None = Header(default=None, alias="X-Esmerelda-User-Id"),
    user_id_query: str | None = Query(default=None, alias="userId"),
) -> int:
    """FastAPI dependency every route in routes.py uses to scope its
    queries. See this module's own docstring for exactly what identity
    guarantee this does — and does not — provide.

    Resolution order: the `X-Esmerelda-User-Id` header (what the frontend
    sends on every request once a user is selected — see
    frontend/src/lib/api.ts), then `?userId=` (for the one plain-link case,
    document downloads), then the demo user — so every existing dev/local
    flow that predates multi-user support keeps working unscoped exactly
    as before, rather than breaking on a missing header."""
    raw = x_esmerelda_user_id or user_id_query
    if raw is not None:
        try:
            candidate_id = int(raw)
        except ValueError:
            candidate_id = None
        if candidate_id is not None:
            user = _get_or_create_user_by_id(db, candidate_id)
            if user is not None:
                return user.id
    return get_or_create_demo_user()
