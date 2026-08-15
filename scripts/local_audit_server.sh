#!/usr/bin/env bash
# Lifecycle helpers for the temporary localhost server used by browser QA.

LOCAL_AUDIT_PID="${LOCAL_AUDIT_PID:-}"

start_local_audit_server() {
  local app_dir="$1" python_bin="$2" port="$3" stdout_path="$4" stderr_path="$5"
  if ! "$python_bin" - "$port" <<'PYCODE'
import socket, sys
with socket.socket() as sock:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
PYCODE
  then
    printf 'local audit port already occupied: %s\n' "$port" >&2
    return 1
  fi
  if [ -n "${LOCAL_AUDIT_PID:-}" ]; then
    printf 'local audit server already tracked: pid=%s\n' "$LOCAL_AUDIT_PID" >&2
    return 2
  fi
  (cd "$app_dir" && exec "$python_bin" -m http.server "$port" --bind 127.0.0.1 >"$stdout_path" 2>"$stderr_path") &
  LOCAL_AUDIT_PID=$!
  sleep 1
  if ! kill -0 "$LOCAL_AUDIT_PID" 2>/dev/null; then
    wait "$LOCAL_AUDIT_PID" || true
    LOCAL_AUDIT_PID=""
    printf 'local audit server failed to start on port %s\n' "$port" >&2
    return 1
  fi
}

stop_local_audit_server() {
  local pid="${LOCAL_AUDIT_PID:-}"
  [ -z "$pid" ] && return 0
  kill -TERM "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  LOCAL_AUDIT_PID=""
}