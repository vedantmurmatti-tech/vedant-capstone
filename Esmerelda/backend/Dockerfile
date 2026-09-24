# Esmerelda backend — production image.
#
# Deliberately excludes Windows-only voice dependencies (pyttsx3) — not
# imported by main.py, api/, storage/, or moodle/sync_service.py at all.
#
# Includes Playwright + a real headless Chromium (see below) because
# moodle/sync_service.py (POST /api/sync/moodle) needs it. An earlier
# version of this Dockerfile excluded Playwright entirely, when nothing
# deployed needed it yet — see BUILD_LOG.md for that decision and this
# one reversing it.
#
# moodle/browser.py IS included (via `COPY moodle/ moodle/` below,
# no longer excluded by .dockerignore) — moodle/sync_service.py's
# run_sync() and api/routes.py's Moodle-connect/session-status endpoints
# import check_moodle_session()/connect_user_interactively() from it at
# runtime (see BUILD_LOG.md's multi-user foundation and deployment-fix
# entries); excluding it previously produced a real
# `ModuleNotFoundError: No module named 'moodle.browser'` on Render the
# first time a multi-user sync or connect request actually reached that
# import. content_radar.py, course_radar.py, submission_radar.py, and
# voice.py remain out of scope for this container (local-only, manually-
# run, interactive prototypes never imported by the deployed API) and
# stay excluded by .dockerignore, as does moodle/browser_profile/ (real
# session cookies — never baked into an image; see get_user_browser_
# profile_dir()/ESMERELDA_DATA_DIR for where a real per-user profile
# actually lives on a deployed instance).
FROM python:3.12-slim

WORKDIR /app

# Installed separately from the rest of the source so this layer is only
# rebuilt when dependencies actually change, not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fetches the actual Chromium browser binary and every Debian system
# library it needs (fonts, graphics libs, etc. — `--with-deps` runs
# `apt-get install` for them) — installing the `playwright` Python
# package alone does not include a browser to drive. Chromium only
# (not the also-available Firefox/WebKit) — the only browser
# moodle/sync_service.py launches (`p.chromium.launch(...)`).
# PLAYWRIGHT_BROWSERS_PATH pins the install location explicitly, so it's
# unambiguous regardless of which user/HOME the container runs as.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium

# api/ and storage/ contain the deployed app; moodle/ contains the sync
# service (moodle/sync_service.py, imported at request time by POST
# /api/sync/moodle) and its two real runtime dependencies,
# document_downloader.py (called from run_sync()) and browser.py
# (check_moodle_session()/connect_user_interactively(), called from
# run_sync() and from api/routes.py's Moodle-connect/session-status
# endpoints — see BUILD_LOG.md). There is no moodle/__init__.py file at
# all (confirmed directly, not assumed) — moodle/ works as a Python 3
# implicit namespace package, which needs no __init__.py to be
# importable; the whole moodle/ directory is copied for simplicity
# rather than naming each of these files individually. Only
# .dockerignore's exclusions (browser_profile/, the standalone
# content_radar.py/course_radar.py/submission_radar.py/voice.py
# prototypes) are left out. main.py
# is the FastAPI entrypoint (moved out of venv/ in an earlier change so
# it's a real, trackable source file instead of living inside a
# gitignored virtualenv). See .dockerignore for what's excluded (the
# root-level Moodle/Playwright scripts, voice.py, the local venv, local
# storage/documents and esmerelda.db, secrets).
COPY main.py .
COPY api/ api/
COPY storage/ storage/
COPY moodle/ moodle/

# storage/paths.py defaults ESMERELDA_DATA_DIR-less resolution to
# backend/storage/ (i.e. /app/storage here) when the env var is unset —
# and that path is NOT persisted across restarts/redeploys on Render (its
# containers use an ephemeral filesystem by default). Set ESMERELDA_DATA_DIR
# to a mounted Render Persistent Disk's path at deploy time for the
# database and downloaded documents to actually survive a redeploy — see
# storage/paths.py, and main.py's startup log, which reports which of
# these two cases is actually in effect on every boot. Not set here: which
# directory to point it at is a Render-dashboard configuration decision
# (attaching a Persistent Disk and setting this env var to its mount
# path), out of scope for this Dockerfile itself.

# Render (like several other container platforms, e.g. Railway) injects
# $PORT at runtime; uvicorn must bind to it, not a fixed port. Shell form
# (not exec-form JSON array) so $PORT is expanded by the shell, and
# 0.0.0.0 so the container is reachable from outside itself.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
