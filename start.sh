#!/usr/bin/env bash
set -euo pipefail

# Render installs Python packages into /opt/render/project/src/.venv, but the
# runtime shell may not activate that virtualenv.  Therefore this script calls
# the virtualenv Python/Gunicorn directly before trying PATH-based commands.
export PORT="${PORT:-8000}"
export WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
export GUNICORN_THREADS="${GUNICORN_THREADS:-4}"
export GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-60}"

ARGS=(
  app:app
  --bind "0.0.0.0:${PORT}"
  --workers "${WEB_CONCURRENCY}"
  --threads "${GUNICORN_THREADS}"
  --timeout "${GUNICORN_TIMEOUT}"
)

if [ -x "/opt/render/project/src/.venv/bin/python" ]; then
  exec /opt/render/project/src/.venv/bin/python -m gunicorn "${ARGS[@]}"
fi

if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python -m gunicorn "${ARGS[@]}"
fi

if [ -x "/opt/render/project/src/.venv/bin/gunicorn" ]; then
  exec /opt/render/project/src/.venv/bin/gunicorn "${ARGS[@]}"
fi

if [ -x ".venv/bin/gunicorn" ]; then
  exec .venv/bin/gunicorn "${ARGS[@]}"
fi

if command -v gunicorn >/dev/null 2>&1; then
  exec gunicorn "${ARGS[@]}"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 -m gunicorn "${ARGS[@]}"
fi

if command -v python >/dev/null 2>&1; then
  exec python -m gunicorn "${ARGS[@]}"
fi

echo "Could not find Render virtualenv, gunicorn, python3, or python." >&2
exit 127
