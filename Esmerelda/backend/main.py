import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as api_router
from storage.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A fresh Railway Volume (or any ESMERELDA_DATA_DIR pointed at an
    # empty directory) has no esmerelda.db and no tables yet — without
    # this, every query would fail with "no such table" on first deploy.
    # create_all() only creates tables that don't already exist, so this
    # is a no-op against the existing local dev database and safe to run
    # on every startup, not just the first one.
    init_db()
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
