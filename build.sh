#!/usr/bin/env bash
set -euo pipefail

# Render's native Python runtime sometimes installs packages into a virtualenv
# that is not available/activated when the service starts.  To avoid every PATH
# and venv issue, install dependencies into a project-local ./vendor directory.
# ./vendor is inside the deployed app, and start.sh loads it with PYTHONPATH.
rm -rf vendor
python3 -m pip install --upgrade pip
python3 -m pip install --target vendor -r requirements.txt
