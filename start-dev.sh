#!/usr/bin/env bash
# One-shot dev launcher — starts backend + frontend in parallel
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$ANTHROPIC_API_KEY" ] && [ -f "$ROOT/.env" ]; then
  export $(grep -v '^#' "$ROOT/.env" | xargs)
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
  exit 1
fi

# ── Backend ──────────────────────────────────────────────────────────────────
echo "► Installing Python deps..."
pip install -q flask requests

echo "► Starting Flask backend on http://localhost:8000"
(cd "$ROOT/backend" && python main.py) &
BACKEND_PID=$!

# ── Frontend ─────────────────────────────────────────────────────────────────
echo "► Installing Node deps..."
(cd "$ROOT/frontend" && npm install --legacy-peer-deps --silent)

echo "► Starting React dev server on http://localhost:3000"
(cd "$ROOT/frontend" && npm start) &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT INT TERM
wait
