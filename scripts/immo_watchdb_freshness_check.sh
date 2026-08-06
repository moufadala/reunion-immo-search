#!/usr/bin/env bash
set -uo pipefail

# Step B: external watcher for production reunion_watch.db freshness + volume.
# Runs independently from the legacy socle_p0 runs chain (--skip-runs).
# Uptime Kuma owns alerting through its dedicated push monitor.

PROJECT="/opt/data/projects/reunion-immo-search"
WATCH_DB="/opt/data/data/reunion_watch.db"
ENV_FILE="/opt/data/services/uptime-kuma/watchdb_push.env"
LOG_DIR="/opt/data/logs"
STATE="/opt/data/artifacts/socle-p0/phase-b/watchdb_freshness_state.json"
mkdir -p "$LOG_DIR" "$(dirname "$STATE")"
LOG="$LOG_DIR/immo_watchdb_freshness_check.log"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cd "$PROJECT" || exit 0
if [ ! -r "$ENV_FILE" ]; then
  echo "[$STAMP] Watch DB freshness env missing or unreadable: $ENV_FILE" >>"$LOG"
  exit 0
fi

python3 freshness_check.py \
  --watch-db "$WATCH_DB" \
  --skip-runs \
  --state "$STATE" \
  --push-env "$ENV_FILE" >>"$LOG" 2>&1
RC=$?
echo "[$STAMP] Watch DB freshness exit=$RC" >>"$LOG"
# Do not mail/spam from cron; Kuma receives explicit up/down signal.
exit 0
