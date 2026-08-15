#!/usr/bin/env bash
set -euo pipefail

umask 077
LOCK_FILE="/tmp/reunion_watch_post_refresh_alerts.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pycache-hermes}"
PROJECT="/opt/data/projects/reunion-immo-search"
LOG_DIR="/opt/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/reunion_watch_post_refresh_alerts.log"
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

{
  echo "[$STAMP] reunion post-refresh alerts start" >&2
  cd "$PROJECT"
  # listing_history runs inside the gated refresh before feed export.
  python3 src/listing_changes.py >>"$LOG" 2>&1
  python3 src/source_health.py >>"$LOG" 2>&1

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Phase C legacy watches retired: ingest.py/check_watches.py intentionally not run from this cron." >>"$LOG"

  python3 src/source_health_alerts.py
  python3 src/search_alerts.py
  rc=$?
  echo "[$STAMP] reunion post-refresh alerts exit=$rc" >&2
  exit "$rc"
} 2>>"$LOG"
