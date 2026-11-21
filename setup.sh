#!/usr/bin/env bash
# Chessy setup: Python 3.11+ venv, dependencies, Stockfish check/install, .env.
# Usage: ./setup.sh [--test]     (--test also runs the pytest suite)
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Python >= 3.11
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "Python 3.11+ not found. macOS: brew install python@3.12   Ubuntu: sudo apt install python3.12 python3.12-venv"
say "Using $($PY --version) ($PY)"

# 2. venv + dependencies
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q -U pip
.venv/bin/python -m pip install -q -r requirements-dev.txt
say "Dependencies installed"

# 3. Stockfish
if command -v stockfish >/dev/null 2>&1 || [ -n "${STOCKFISH_PATH:-}" ]; then
  say "Stockfish found: $(command -v stockfish || echo "$STOCKFISH_PATH")"
else
  say "Stockfish not found; installing"
  if command -v brew >/dev/null 2>&1; then brew install stockfish
  elif command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y stockfish
  elif command -v dnf >/dev/null 2>&1; then sudo dnf install -y stockfish
  else die "Install Stockfish manually (https://stockfishchess.org/download/) and set STOCKFISH_PATH in .env"; fi
fi
# on Debian/Ubuntu the binary lives in /usr/games, which is often not on PATH
if ! command -v stockfish >/dev/null 2>&1 && [ -x /usr/games/stockfish ]; then
  say "Found /usr/games/stockfish; add 'STOCKFISH_PATH=/usr/games/stockfish' to .env"
fi

# 4. .env
[ -f .env ] || { cp .env.example .env; say "Created .env (edit it to add an AI coach key)"; }

# 5. optional tests
if [ "${1:-}" = "--test" ]; then say "Running tests"; .venv/bin/python -m pytest -q; fi

say "Done. Start the server with:  .venv/bin/uvicorn app.main:app --port 8765 --reload   then open http://localhost:8765"
