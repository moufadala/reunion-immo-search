import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
LIB = ROOT / "scripts" / "local_audit_server.sh"
BASH = os.environ.get("BASH_EXE", "bash")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _run_case(tmp_path, ending):
    port = _free_port()
    pid_file = tmp_path / "pid"
    script = tmp_path / "case.sh"
    script.write_text(f'''set -eu
source "{LIB.as_posix()}"
trap stop_local_audit_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
start_local_audit_server "{tmp_path.as_posix()}" "{sys.executable}" "{port}" "{(tmp_path/'out').as_posix()}" "{(tmp_path/'err').as_posix()}"
printf '%s' "$LOCAL_AUDIT_PID" > "{pid_file.as_posix()}"
{ending}
''', encoding="utf-8")
    proc = subprocess.Popen([BASH, str(script)])
    for _ in range(50):
        if pid_file.exists():
            break
        time.sleep(0.1)
    pid = int(pid_file.read_text())
    return proc, pid


def test_server_cleanup_on_success(tmp_path):
    proc, pid = _run_case(tmp_path, "stop_local_audit_server")
    assert proc.wait(timeout=10) == 0
    assert not _alive(pid)


def test_server_cleanup_on_failure(tmp_path):
    proc, pid = _run_case(tmp_path, "exit 7")
    assert proc.wait(timeout=10) == 7
    assert not _alive(pid)


def test_server_cleanup_on_termination(tmp_path):
    proc, pid = _run_case(tmp_path, "sleep 30")
    proc.terminate()
    assert proc.wait(timeout=10) != 0
    assert not _alive(pid)


def test_pipeline_stops_server_immediately_after_browser_audits():
    source = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    assert 'source "$PROJECT/scripts/local_audit_server.sh"' in source
    start = source.index("start_local_audit_server")
    last_audit = source.index("run_step public_changes_filter_audit")
    stop = source.index("stop_local_audit_server", last_audit)
    assert start < last_audit < stop < source.index("run_step daily_summary")
    assert "trap 'exit 130' INT" in source
    assert "trap 'exit 143' TERM" in source

def test_start_fails_when_port_is_owned_by_an_old_server(tmp_path):
    port = _free_port()
    old = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("old server did not bind")
        script = tmp_path / "collision.sh"
        script.write_text(f'''set -eu
source "{LIB.as_posix()}"
trap stop_local_audit_server EXIT
start_local_audit_server "{tmp_path.as_posix()}" "{sys.executable}" "{port}" "{(tmp_path/'new.out').as_posix()}" "{(tmp_path/'new.err').as_posix()}"
''', encoding="utf-8")
        proc = subprocess.run([BASH, str(script)], timeout=10)
        assert proc.returncode != 0
        assert old.poll() is None
    finally:
        old.terminate()
        old.wait(timeout=10)
