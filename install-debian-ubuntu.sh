#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1 || ! python3 -m venv --help >/dev/null 2>&1 || { ! command -v libreoffice >/dev/null 2>&1 && ! command -v soffice >/dev/null 2>&1; }; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip libreoffice
fi

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
echo "Done."
echo "CLI: .venv/bin/python parasha_generator.py 2026-08-29"
echo "Web: PARASHA_HOST=0.0.0.0 PARASHA_PORT=8000 .venv/bin/python web_server.py"
