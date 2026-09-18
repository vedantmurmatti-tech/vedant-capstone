# Esmerelda backend — production image.
#
# Deliberately excludes Playwright and Windows-only voice dependencies:
# only backend/requirements.txt is installed, and that file already
# excludes them (see its own header comment for why). Moodle/Playwright
# automation (moodle/, content_radar.py, course_radar.py,
# submission_radar.py, voice.py, browser_profile/) is out of scope for
# this container — it's a local-only, manually-run workflow, not part of
# the deployed API.
FROM python:3.12-slim

WORKDIR /app

# Installed separately from the rest of the source so this layer is only
# rebuilt when dependencies actually change, not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# api/ and storage/ contain the deployed app; main.py is the FastAPI
# entrypoint (moved out of venv/ in this change so it's a real, trackable
# source file instead of living inside a gitignored virtualenv). See
# .dockerignore for what's excluded (Moodle/Playwright code, the local
# venv, local storage/documents and esmerelda.db, secrets).
COPY main.py .
COPY api/ api/
COPY storage/ storage/

# storage/paths.py defaults ESMERELDA_DATA_DIR-less resolution to
# backend/storage/ (i.e. /app/storage here) when the env var is unset;
# set ESMERELDA_DATA_DIR (e.g. to a mounted Railway Volume path) at
# deploy time for data to persist across redeploys — see storage/paths.py.
# Not set here: which directory to point it at is a Railway-config
# decision, out of scope for this Dockerfile.

# Railway injects $PORT at runtime; uvicorn must bind to it, not a fixed
# port. Shell form (not exec-form JSON array) so $PORT is expanded by the
# shell, and 0.0.0.0 so the container is reachable from outside itself.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
