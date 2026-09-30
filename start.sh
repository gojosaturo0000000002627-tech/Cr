#!/usr/bin/env bash
set -euo pipefail

# Render installs requirements into .venv during build. Use that exact Gunicorn.
export PORT="${PORT:-8000}"

if [ -x ".venv/bin/gunicorn" ]; then
  exec .venv/bin/gunicorn app:app --bind "0.0.0.0:${PORT}" --workers "${WEB_CONCURRENCY:-1}" --threads "${GUNICORN_THREADS:-4}" --timeout "${GUNICORN_TIMEOUT:-120}"
fi

if [ -x "/opt/render/project/src/.venv/bin/gunicorn" ]; then
  exec /opt/render/project/src/.venv/bin/gunicorn app:app --bind "0.0.0.0:${PORT}" --workers "${WEB_CONCURRENCY:-1}" --threads "${GUNICORN_THREADS:-4}" --timeout "${GUNICORN_TIMEOUT:-120}"
fi

echo "Gunicorn was not found in Render virtualenv. Installing requirements now..." >&2
python3 -m pip install -r requirements.txt
exec python3 -m gunicorn app:app --bind "0.0.0.0:${PORT}" --workers "${WEB_CONCURRENCY:-1}" --threads "${GUNICORN_THREADS:-4}" --timeout "${GUNICORN_TIMEOUT:-120}"
