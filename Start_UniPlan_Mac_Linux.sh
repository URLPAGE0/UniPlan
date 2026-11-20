#!/usr/bin/env sh
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo 'Install Python 3 first.'; exit 1; }
if [ ! -f .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --open-browser
