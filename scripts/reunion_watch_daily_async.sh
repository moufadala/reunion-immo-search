#!/usr/bin/env bash
set -euo pipefail

umask 077
# Hermes no-agent cron does not inherit the interactive terminal env.
# Load shared secrets/env for the detached daily worker without printing them.
if [ -r /opt/data/.env ]; then
  set -a
  . /opt/data/.env
  set +a
fi
BASE_DIR="/opt/data/artifacts/reunion-watch-async"
LOG_DIR="/opt/data/logs"
mkdir -p "$BASE_DIR" "$LOG_DIR"

# Prevent multiple long workers from stacking if a previous daily run is still active.
LOCK_FILE="/tmp/reunion_watch_daily_async_launcher.lock"
exec 8>"$LOCK_FILE"
if ! flock -n 8; then
  exit 0
fi

# If a worker is already running, stay silent: cron/no-agent must not spam.
for status in "$BASE_DIR"/*.status; do
  [ -e "$status" ] || continue
  if grep -q '^state=running$' "$status"; then
    pid=$(awk -F= '$1=="pid"{print $2}' "$status" | tail -1)
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      exit 0
    fi
  fi
done

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR="$BASE_DIR/$STAMP"
mkdir -p "$RUN_DIR"
STATUS_FILE="$RUN_DIR/run.status"
STDOUT_FILE="$RUN_DIR/stdout.txt"
STDERR_FILE="$RUN_DIR/stderr.txt"
WORKER_LOG="$LOG_DIR/reunion_watch_daily_async_${STAMP}.log"

{
  echo "state=running"
  echo "stamp=$STAMP"
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "run_dir=$RUN_DIR"
} > "$STATUS_FILE"

WORKER_CMD="${REUNION_WATCH_DAILY_WORKER:-/opt/data/scripts/reunion_watch_daily.sh}"

(
  set +e
  bash -lc "$WORKER_CMD" >"$STDOUT_FILE" 2>"$STDERR_FILE"
  code=$?
  {
    echo "state=finished"
    echo "stamp=$STAMP"
    echo "started_at=$(awk -F= '$1=="started_at"{print $2}' "$STATUS_FILE" | tail -1)"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "exit_code=$code"
    echo "run_dir=$RUN_DIR"
    echo "stdout_file=$STDOUT_FILE"
    echo "stderr_file=$STDERR_FILE"
  } > "$STATUS_FILE.tmp"
  mv "$STATUS_FILE.tmp" "$STATUS_FILE"
  exit 0
) >"$WORKER_LOG" 2>&1 < /dev/null &
pid=$!
# Detach all output from cron; the completion notifier delivers the final summary.
disown "$pid" 2>/dev/null || true
printf 'pid=%s\n' "$pid" >> "$STATUS_FILE"
exit 0
