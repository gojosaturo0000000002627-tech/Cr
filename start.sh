#!/usr/bin/env bash
set -euo pipefail

export PORT="${PORT:-8000}"

# Use project-local dependencies created by build.sh. If Render ever starts
# without the build artifact, install them once at startup as a fallback.
if [ ! -d "vendor/gunicorn" ] || [ ! -d "vendor/flask" ]; then
  echo "vendor dependencies missing; installing into ./vendor now..." >&2
  rm -rf vendor
  python3 -m pip install --target vendor -r requirements.txt
fi

export PYTHONPATH="$(pwd)/vendor:${PYTHONPATH:-}"
exec python3 -m gunicorn app:app \
  --bind "0.0.0.0:${PORT}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-120}"
