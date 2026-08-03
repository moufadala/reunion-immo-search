#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/opt/data/artifacts/reunion-watch-async"
mkdir -p "$BASE_DIR"
now_epoch=$(date -u +%s)

shopt -s nullglob
for status in "$BASE_DIR"/*/run.status; do
  dir=$(dirname "$status")
  reported="$dir/reported.ok"
  [ ! -e "$reported" ] || continue

  state=$(awk -F= '$1=="state"{print $2}' "$status" | tail -1)
  stamp=$(awk -F= '$1=="stamp"{print $2}' "$status" | tail -1)
  started_at=$(awk -F= '$1=="started_at"{print $2}' "$status" | tail -1)
  stdout_file=$(awk -F= '$1=="stdout_file"{print $2}' "$status" | tail -1)
  stderr_file=$(awk -F= '$1=="stderr_file"{print $2}' "$status" | tail -1)
  exit_code=$(awk -F= '$1=="exit_code"{print $2}' "$status" | tail -1)

  if [ "$state" = "finished" ]; then
    {
      if [ "${exit_code:-1}" = "0" ]; then
        if [ -n "${stdout_file:-}" ] && [ -s "$stdout_file" ]; then
          sed -n '1,80p' "$stdout_file"
        else
          echo "RUN Watch quotidien terminé OK, mais stdout vide."
        fi
        alerts_tmp="$dir/reunion_watch_post_refresh_alerts.out"
        if /opt/data/scripts/reunion_watch_post_refresh_alerts.sh >"$alerts_tmp" 2>"$dir/reunion_watch_post_refresh_alerts.err"; then
          if [ -s "$alerts_tmp" ]; then
            echo
            echo "Alertes immo post-refresh:"
            sed -n '1,80p' "$alerts_tmp"
          fi
        else
          echo
          echo "ALERTE: reunion_watch_post_refresh_alerts.sh a échoué après le refresh."
          tail -40 "$dir/reunion_watch_post_refresh_alerts.err" 2>/dev/null || true
        fi
      else
        echo "ALERTE RUN Watch quotidien: échec worker async (exit_code=${exit_code:-unknown}, stamp=${stamp:-unknown})"
        echo "Status: $status"
        if [ -n "${stdout_file:-}" ] && [ -s "$stdout_file" ]; then
          echo
          echo "Stdout:"
          sed -n '1,80p' "$stdout_file"
        fi
        if [ -n "${stderr_file:-}" ] && [ -s "$stderr_file" ]; then
          echo
          echo "Stderr tail:"
          tail -40 "$stderr_file"
        fi
      fi
    }
    touch "$reported"
    exit 0
  fi

  # Stale running worker: alert once after 2h.
  stale_marker="$dir/reported-stale.ok"
  [ ! -e "$stale_marker" ] || continue
  if [ -n "${started_at:-}" ]; then
    started_epoch=$(date -u -d "$started_at" +%s 2>/dev/null || echo 0)
    age=$((now_epoch - started_epoch))
    if [ "$started_epoch" -gt 0 ] && [ "$age" -gt 7200 ]; then
      echo "ALERTE RUN Watch quotidien: worker async toujours running après ${age}s (stamp=${stamp:-unknown})"
      echo "Status: $status"
      touch "$stale_marker"
      exit 0
    fi
  fi
done

# Silence means no completed/unreported run.
exit 0
