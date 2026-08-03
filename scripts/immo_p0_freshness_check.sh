#!/usr/bin/env bash
set -uo pipefail

# Phase D P0: external freshness/D2bis checker.
# Must be launched by system cron (outside Hermes). Silent stdout by default;
# Uptime Kuma owns Telegram alerting via push status up/down.

PROJECT="/opt/data/projects/reunion-immo-search"
DB="$PROJECT/data/socle_p0.sqlite"
ENV_FILE="/opt/data/services/uptime-kuma/phased_push.env"
LOG_DIR="/opt/data/logs"
STATE="/opt/data/artifacts/socle-p0/phase-d/freshness_state.json"
mkdir -p "$LOG_DIR" "$(dirname "$STATE")"
LOG="$LOG_DIR/immo_p0_freshness_check.log"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cd "$PROJECT" || exit 0
if [ ! -r "$ENV_FILE" ]; then
  echo "[$STAMP] Phase D freshness env missing: $ENV_FILE" >>"$LOG"
  exit 0
fi

python3 freshness_check.py --db "$DB" --state "$STATE" --push-env "$ENV_FILE" >>"$LOG" 2>&1
RC=$?
echo "[$STAMP] Phase D freshness exit=$RC" >>"$LOG"
# Do not mail/spam from cron; Kuma receives explicit up/down signal.
exit 0
