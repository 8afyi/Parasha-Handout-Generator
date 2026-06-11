#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v brew >/dev/null 2>&1; then
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [[ -x /usr/local/bin/brew ]] && eval "$(/usr/local/bin/brew shellenv)"
fi

command -v python3 >/dev/null 2>&1 || brew install python
if ! command -v soffice >/dev/null 2>&1 && [[ ! -x /Applications/LibreOffice.app/Contents/MacOS/soffice ]]; then
  brew install --cask libreoffice
fi

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
echo "Done. Run: .venv/bin/python parasha_generator.py 2026-08-29"
