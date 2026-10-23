#!/usr/bin/env bash
# Starts Flowwright on macOS / Linux in this terminal; Ctrl+C stops both.
#   API      http://127.0.0.1:8001   (reloads when Python code changes)
#   Web app  http://127.0.0.1:5173   (open this in your browser)
# Usage, from the repository root (after ./scripts/setup.sh):  ./scripts/start.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

python="$root/.venv/bin/python"
[ -x "$python" ] || python="$root/.venv/Scripts/python.exe"  # Git Bash on Windows
[ -x "$python" ] || { echo "No .venv found. Run ./scripts/setup.sh first." >&2; exit 1; }
[ -f "$root/.env" ] || { echo "No .env found. Run ./scripts/setup.sh first." >&2; exit 1; }
[ -d "$root/frontend/react-app/node_modules" ] || { echo "Frontend not installed. Run ./scripts/setup.sh first." >&2; exit 1; }

(cd "$root" && "$python" -m uvicorn app.main:app --port 8001 --reload) &
api=$!
trap 'kill "$api" 2>/dev/null || true' EXIT INT TERM

echo "API      http://127.0.0.1:8001"
echo "Web app  http://127.0.0.1:5173   <- open this"
cd "$root/frontend/react-app" && npm run dev
