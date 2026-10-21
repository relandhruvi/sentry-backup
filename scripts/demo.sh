#!/usr/bin/env bash
# Start the Scam Shield demo: backend (with model warm-up) + frontend, then open /demo.
# Ctrl-C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PORT="${PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
URL="http://127.0.0.1:$PORT"
LOG="$BACKEND/data/demo-backend.log"

[[ -x "$BACKEND/.venv/bin/python" ]] || (cd "$BACKEND" && make setup)
[[ -d "$FRONTEND/node_modules" ]] || (cd "$FRONTEND" && npm install --no-audit --no-fund)
if lsof -tnP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null; then
  echo "port $PORT is busy (old server?). Stop it with: kill \$(lsof -tnP -iTCP:$PORT -sTCP:LISTEN)"
  exit 1
fi

mkdir -p "$BACKEND/data"
(cd "$BACKEND" && PORT="$PORT" exec .venv/bin/python -m app) >"$LOG" 2>&1 &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT INT TERM
echo "backend starting (log: $LOG)"

# Warm-up: wait until speech-to-text has loaded and run its first inference, and the classifier
# has loaded. Whatever /health reports is printed as is.
for _ in $(seq 1 180); do
  if ! kill -0 $BACKEND_PID 2>/dev/null; then echo "backend exited; see $LOG"; exit 1; fi
  HEALTH="$(curl -fs "$URL/health" || true)"
  STT="$(printf '%s' "$HEALTH" | "$BACKEND/.venv/bin/python" -c 'import json,sys
try: c=json.load(sys.stdin)["components"]; print(c["stt"]["status"], c["classifier"]["status"])
except Exception: print("starting starting")')"
  set -- $STT
  printf '\r  stt=%-12s classifier=%-12s' "$1" "$2"
  if [[ "$1" == "ok" && "$2" != "loading" && "$2" != "starting" ]]; then break; fi
  sleep 1
done
echo
curl -fs "$URL/health" | "$BACKEND/.venv/bin/python" -c 'import json,sys
b=json.load(sys.stdin); print("health:", b["status"])
for k,v in b["components"].items(): print("  " + k.ljust(11), v["status"].ljust(12), v["detail"])'

echo "frontend: http://localhost:$FRONTEND_PORT/demo"
[[ -n "${DEMO_NO_OPEN:-}" ]] || ( sleep 2 && command -v open >/dev/null && open "http://localhost:$FRONTEND_PORT/demo" ) &
cd "$FRONTEND" && SCAMSHIELD_BACKEND="$URL" SCAMSHIELD_FRONTEND_PORT="$FRONTEND_PORT" npm run dev
