#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${REUNION_WATCH_ASYNC_DIR:-/opt/data/artifacts/reunion-watch-async}"
POST_REFRESH_ALERTS="${REUNION_WATCH_POST_REFRESH_ALERTS:-/opt/data/scripts/reunion_watch_post_refresh_alerts.sh}"
NOTIFY_SINK="${REUNION_WATCH_NOTIFY_SINK:-}"
mkdir -p "$BASE_DIR"
now_epoch=$(date -u +%s)

deliver_notification() {
  if [ -n "$NOTIFY_SINK" ]; then
    if "$NOTIFY_SINK"; then
      return 0
    else
      sink_exit=$?
      echo "notification delivery failed (sink_exit=$sink_exit)" >&2
      return 75
    fi
  fi

  # Explicit fallback for cron/mail or an interactive caller.
  cat
}

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
    if [ "${exit_code:-1}" = "0" ]; then
      alerts_tmp="$dir/reunion_watch_post_refresh_alerts.out"
      alerts_err="$dir/reunion_watch_post_refresh_alerts.err"
      if "$POST_REFRESH_ALERTS" >"$alerts_tmp" 2>"$alerts_err"; then
        notification_tmp="$dir/success.notification"
        {
          if [ -n "${stdout_file:-}" ] && [ -s "$stdout_file" ]; then
            sed -n '1,80p' "$stdout_file"
          else
            echo "RUN Watch quotidien terminé OK, mais stdout vide."
          fi
          if [ -s "$alerts_tmp" ]; then
            echo
            echo "Alertes immo post-refresh:"
            sed -n '1,80p' "$alerts_tmp"
          fi
        } >"$notification_tmp"
      else
        post_refresh_exit=$?
        if [ "$post_refresh_exit" = "75" ]; then
          tail -40 "$alerts_err" >&2 2>/dev/null || true
          exit 75
        fi
        notification_tmp="$dir/post-refresh-failure.notification"
        {
          echo "ALERTE post-refresh: reunion_watch_post_refresh_alerts.sh a échoué après le refresh."
          echo "Status: $status"
          echo "Exit code: $post_refresh_exit"
          tail -40 "$alerts_err" 2>/dev/null || true
        } >"$notification_tmp"
      fi
    else
      notification_tmp="$dir/worker-failure.notification"
      {
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
      } >"$notification_tmp"
    fi

    if deliver_notification <"$notification_tmp"; then
      touch "$reported"
      exit 0
    else
      delivery_exit=$?
      exit "$delivery_exit"
    fi
  fi

  # Stale running worker: alert once after 2h.
  stale_marker="$dir/reported-stale.ok"
  [ ! -e "$stale_marker" ] || continue
  if [ -n "${started_at:-}" ]; then
    started_epoch=$(date -u -d "$started_at" +%s 2>/dev/null || echo 0)
    age=$((now_epoch - started_epoch))
    if [ "$started_epoch" -gt 0 ] && [ "$age" -gt 7200 ]; then
      notification_tmp="$dir/stale-worker.notification"
      {
        echo "ALERTE RUN Watch quotidien: worker async toujours running après ${age}s (stamp=${stamp:-unknown})"
        echo "Status: $status"
      } >"$notification_tmp"
      if deliver_notification <"$notification_tmp"; then
        touch "$stale_marker"
        exit 0
      else
        delivery_exit=$?
        exit "$delivery_exit"
      fi
    fi
  fi
done

# Silence means no completed/unreported run.
exit 0
