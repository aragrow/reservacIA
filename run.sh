#!/usr/bin/env bash
# Background runner for the reservacIA API.
#
# Usage:
#   ./run.sh           # stop-if-running, then start in background (default)
#   ./run.sh start     # same as no-arg
#   ./run.sh stop      # stop the running instance
#   ./run.sh restart   # same as start (kept for convention)
#   ./run.sh status    # report pid + port if running
#   ./run.sh logs      # tail the log file
#
# Env overrides: RESERVACIA_HOST, RESERVACIA_PORT
set -euo pipefail
cd "$(dirname "$0")"

HOST="${RESERVACIA_HOST:-0.0.0.0}"
PORT="${RESERVACIA_PORT:-8765}"
PID_FILE="./data/run.pid"
LOG_FILE="./data/run.log"

# 0.0.0.0 is a bind address, not a URL host — show localhost in printed links.
DOCS_HOST="$HOST"
[[ "$DOCS_HOST" == "0.0.0.0" ]] && DOCS_HOST="localhost"
DOCS_URL="http://${DOCS_HOST}:${PORT}/docs"

mkdir -p ./data

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found on PATH — install from https://docs.astral.sh/uv/" >&2
  exit 1
fi

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

stop() {
  if ! is_running; then
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.2
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  echo "stopped previous instance (was pid $pid)"
}

start() {
  # Always stop any prior instance first so start is idempotent.
  stop
  nohup uv run --no-sync uvicorn app.main:app --host "$HOST" --port "$PORT" \
        >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  sleep 0.5
  if is_running; then
    echo "started (pid $(cat "$PID_FILE")) on ${HOST}:${PORT} — logs: $LOG_FILE"
    echo "docs: $DOCS_URL"
  else
    echo "failed to start — see $LOG_FILE" >&2
    rm -f "$PID_FILE"
    exit 1
  fi
}

status() {
  if is_running; then
    echo "running (pid $(cat "$PID_FILE")) on ${HOST}:${PORT}"
    echo "docs: $DOCS_URL"
  else
    echo "not running"
  fi
}

cmd="${1:-start}"
case "$cmd" in
  start|restart) start ;;
  stop)          stop; echo "done" ;;
  status)        status ;;
  logs)          tail -f "$LOG_FILE" ;;
  *) echo "usage: $0 {start|stop|restart|status|logs}" >&2; exit 2 ;;
esac
