import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as api_router
from storage.database import init_db

# Without this, every logger.info() call in this app (main.py's own
# startup logs, api/routes.py's "esmerelda.sync", moodle/sync_service.py's
# "esmerelda.moodle_sync") is silently dropped when run via the real
# production command (`uvicorn main:app ...`, no --log-config) — verified
# directly, not assumed: running the real server without this line and
# hitting it produced zero output from any of our own loggers in the
# server's log stream, even though the code was genuinely executing
# (confirmed via the API's own responses). Uvicorn configures handlers
# for its *own* "uvicorn"/"uvicorn.error"/"uvicorn.access" loggers, but
# never touches the root logger our loggers otherwise fall through to
# with no handler of its own — so every one of this change's new
# "[BUILD VERSION]"/"[SYNC DEBUG]"/diagnostic log lines would have been
# invisible in Render's log stream regardless of whether the rest of the
# sync/diagnostic code was even working. basicConfig() is a no-op if the
# root logger already has a handler, so this is safe to call
# unconditionally at import time.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,  # explicit — logging's own default is stderr, which most log
                        # viewers capture too, but pinning this avoids any ambiguity
                        # about where these lines actually land.
)

logger = logging.getLogger("esmerelda.startup")


def _find_sync_routes(app: FastAPI) -> list[str]:
    """Walks app.routes to find every registered path containing "sync" —
    used only for the temporary startup diagnostic log below (see
    BUILD_LOG.md). Not a naive `route.path` scan: this FastAPI version
    (0.141.1) wraps an included APIRouter's routes in an internal
    `_IncludedRouter` object that has no `.path` of its own — its real
    routes live on `.original_router.routes`. A naive scan silently
    returns an empty list here (confirmed directly, not assumed — this
    was caught before shipping), which would have made this diagnostic
    log claim no sync routes were registered even when they genuinely
    were. Real FastAPI/Starlette `Route`/`APIRoute` objects (like `/` and
    `/health`, defined directly on `app`) are also handled directly."""
    found: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is not None:
            if "sync" in path:
                found.append(path)
            continue
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            for sub_route in getattr(original_router, "routes", []):
                sub_path = getattr(sub_route, "path", None)
                if sub_path and "sync" in sub_path:
                    found.append(sub_path)
    return sorted(found)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A fresh Railway Volume (or any ESMERELDA_DATA_DIR pointed at an
    # empty directory) has no esmerelda.db and no tables yet — without
    # this, every query would fail with "no such table" on first deploy.
    # create_all() only creates tables that don't already exist, so this
    # is a no-op against the existing local dev database and safe to run
    # on every startup, not just the first one.
    init_db()

    # TEMPORARY diagnostic logging (see BUILD_LOG.md) — a clearly visible,
    # unique-per-build marker so it's possible to confirm from a running
    # instance's own log stream (e.g. on Render) exactly which build of
    # the Moodle sync/diagnostic code is actually running, and to confirm
    # the sync diagnostics route is genuinely registered in *this*
    # running app, not just present in the source tree. Import is lazy
    # and guarded so a deployment without Playwright installed still
    # starts up cleanly and just skips this one log line.
    try:
        from moodle.sync_service import DIAGNOSTIC_BUILD_MARKER
        logger.info("[BUILD VERSION] Moodle diagnostic build %s", DIAGNOSTIC_BUILD_MARKER)
    except Exception as exc:
        logger.warning("[BUILD VERSION] could not import moodle.sync_service to report a build marker: %s", exc)

    logger.info("[STARTUP] registered sync-related routes: %s", _find_sync_routes(app))

    yield


app = FastAPI(title="Esmerelda API", lifespan=lifespan)

# Local dev: the Vite frontend runs on a variable localhost port (5173 by
# default, but shifts up if that port is busy) — always allowed.
#
# Production: the deployed frontend's real origin (e.g. a Railway/static
# host URL) is supplied via FRONTEND_ORIGIN rather than hardcoded, since
# it isn't known yet and shouldn't require an app code change once it is.
# Unset in local dev — only localhost is allowed then, unchanged behavior.
_allow_origins = []
_frontend_origin = os.environ.get("FRONTEND_ORIGIN")
if _frontend_origin:
    _allow_origins.append(_frontend_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
def root():
    return {
        "message": "Esmerelda backend is running"
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }
