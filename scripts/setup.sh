#!/usr/bin/env bash
# One-time setup for Flowwright on macOS / Linux. Safe to run again: finished steps are skipped.
#   1. Creates the Python virtual environment (.venv) and installs the backend and test dependencies.
#   2. Creates .env from .env.example, with a freshly generated AUTH_SECRET_KEY.
#   3. Installs the React app's dependencies.
# Usage, from the repository root:  ./scripts/setup.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

# -- Python backend ----------------------------------------------------------------------------------------
command -v python3 >/dev/null || { echo "Python not found. Install Python 3.11+ from https://www.python.org" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || { echo "Python 3.11 or newer is required (found: $(python3 --version))." >&2; exit 1; }

if [ ! -x .venv/bin/python ]; then
  step "Creating the virtual environment (.venv)"
  python3 -m venv .venv
fi
step "Installing backend dependencies"
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements-dev.txt

# -- Configuration -----------------------------------------------------------------------------------------
if [ ! -f .env ]; then
  step "Creating .env with a new AUTH_SECRET_KEY"
  secret="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  sed "s|^AUTH_SECRET_KEY=\$|AUTH_SECRET_KEY=${secret}|" .env.example > .env
else
  echo "==> .env already exists: left unchanged"
fi

# -- React frontend ----------------------------------------------------------------------------------------
command -v npm >/dev/null || { echo "npm not found. Install Node.js 20+ (LTS) from https://nodejs.org" >&2; exit 1; }
step "Installing frontend dependencies"
npm install --prefix frontend/react-app --no-fund --no-audit

# -- Done --------------------------------------------------------------------------------------------------
printf '\n\033[32mSetup complete.\033[0m\n'
grep -qE '^GROQ_API_KEY=.+' .env \
  || printf '\033[33mNext: put your Groq API key in .env (GROQ_API_KEY=...). Get one at https://console.groq.com/keys\033[0m\n'
echo "Then start the app with:  ./scripts/start.sh"
