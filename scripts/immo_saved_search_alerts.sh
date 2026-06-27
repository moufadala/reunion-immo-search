#!/usr/bin/env bash
set -euo pipefail

# Safe operator wrapper for src/search_alerts.py.
# This script never sends Telegram by itself; live mode prints a digest to stdout
# for an external, explicitly-enabled sender to consume.

umask 077
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/pycache-hermes}"

PROJECT="${IMMO_ALERT_PROJECT:-/opt/data/projects/reunion-immo-search}"
CONFIG="${IMMO_ALERT_CONFIG:-$PROJECT/config/saved_searches.json}"
PYTHON="${PYTHON:-python3}"
MODE="${1:-preview}"
shift || true

case "$MODE" in
  preview)
    exec "$PYTHON" "$PROJECT/src/search_alerts.py" --config "$CONFIG" --preview "$@"
    ;;
  status)
    exec "$PYTHON" "$PROJECT/src/search_alerts.py" --config "$CONFIG" --status "$@"
    ;;
  bootstrap-silent)
    exec "$PYTHON" "$PROJECT/src/search_alerts.py" --config "$CONFIG" --bootstrap-silent "$@"
    ;;
  live-once)
    : "${IMMO_ALERT_LIVE_CONFIRM:?Set IMMO_ALERT_LIVE_CONFIRM=RUN_LIVE_ONCE to enable controlled live digest output}"
    if [ "$IMMO_ALERT_LIVE_CONFIRM" != "RUN_LIVE_ONCE" ]; then
      printf 'Refusing live-once: IMMO_ALERT_LIVE_CONFIRM must equal RUN_LIVE_ONCE\n' >&2
      exit 64
    fi
    exec "$PYTHON" "$PROJECT/src/search_alerts.py" --config "$CONFIG" --live "$@"
    ;;
  json-preview)
    exec "$PYTHON" "$PROJECT/src/search_alerts.py" --config "$CONFIG" --dry-run "$@"
    ;;
  *)
    cat >&2 <<'USAGE'
Usage: scripts/immo_saved_search_alerts.sh MODE [extra search_alerts args]

Modes:
  preview            Human-readable dry preview; no state write, no Telegram.
  json-preview       JSON dry-run; no state write, no Telegram.
  status             Human-readable state/config status; no state write.
  bootstrap-silent   Explicitly writes/refreshes baseline silently, with backup if state exists.
  live-once          Controlled one-shot digest output. Requires IMMO_ALERT_LIVE_CONFIRM=RUN_LIVE_ONCE.

Useful extra args:
  --max-emit-total N  Cap live digest new+event count (default: 10); exits without writing if exceeded.
  --format json       Machine-readable output for preview/status/bootstrap.
USAGE
    exit 64
    ;;
esac
