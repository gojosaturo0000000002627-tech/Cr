#!/usr/bin/env bash
set -euo pipefail

# Render sometimes exposes Python as python3 instead of python.  This script
# installs dependencies with whichever Python executable is available.
if command -v python3 >/dev/null 2>&1; then
  python3 -m pip install --upgrade pip
  python3 -m pip install -r requirements.txt
elif command -v python >/dev/null 2>&1; then
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
else
  # Last fallback for Render images where pip is available but python is not on PATH.
  pip install -r requirements.txt
fi
