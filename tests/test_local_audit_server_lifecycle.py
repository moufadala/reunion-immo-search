import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
LIB = ROOT / "scripts" / "local_audit_server.sh"
BASH = os.environ.get("BASH_EXE", "bash")


def _bash_path(path):
    resolved = str(Path(path).resolve())
    if os.name != "nt":
        return Path(resolved).as_posix()
    drive, tail = os.path.splitdrive(resolved)
    if not drive:
        raise RuntimeError(f"Windows path has no drive: {resolved}")
    kernel = subprocess.run(
        [BASH, "-lc", "uname -s"], capture_output=True, text=True, check=True
    ).stdout.strip().lower()
    prefix = f"/mnt/{drive[0].lower()}" if kernel == "linux" else f"/{drive[0].lower()}"
    return prefix + tail.replace("\\", "/")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _alive(pid):
    return subprocess.run(
        [BASH, "-c", f"kill -0 {pid} 2>/dev/null"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _read_numeric_pid(path):
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return int(value) if value.isdigit() else None


def _cleanup_process(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _wait_for_pid_files(proc, pid_files, timeout=5):
    deadline = time.monotonic() + timeout
    values = tuple(_read_numeric_pid(path) for path in pid_files)
    while time.monotonic() < deadline:
        if all(value is not None for value in values):
            return values
        time.sleep(0.05)
        values = tuple(_read_numeric_pid(path) for path in pid_files)

    if all(value is not None for value in values):
        return values

    _cleanup_process(proc)
    states = ", ".join(
        f"{path.name}={value!r}" for path, value in zip(pid_files, values)
    )
    raise AssertionError(f"timed out waiting for numeric PID files: {states}")


def test_wait_for_pid_files_requires_numeric_content(tmp_path):
    pid_file = tmp_path / "pid"
    shell_pid_file = tmp_path / "shell_pid"
    pid_file.write_text("")
    shell_pid_file.write_text("")

    def publish_pids():
        time.sleep(0.05)
        pid_file.write_text("123")
        shell_pid_file.write_text("456")

    writer = threading.Thread(target=publish_pids)
    writer.start()
    try:
        assert _wait_for_pid_files(None, (pid_file, shell_pid_file), timeout=1) == (
            123,
            456,
        )
    finally:
        writer.join(timeout=1)


def test_wait_for_pid_files_cleans_up_process_on_timeout(tmp_path):
    pid_file = tmp_path / "pid"
    shell_pid_file = tmp_path / "shell_pid"
    pid_file.write_text("")
    shell_pid_file.write_text("")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    with pytest.raises(AssertionError, match="numeric PID files"):
        _wait_for_pid_files(proc, (pid_file, shell_pid_file), timeout=0.05)

    assert proc.poll() is not None


def _run_case(tmp_path, ending):
    port = _free_port()
    pid_file = tmp_path / "pid"
    shell_pid_file = tmp_path / "shell_pid"
    script = tmp_path / "case.sh"
    script.write_text(f'''set -eu
source "{_bash_path(LIB)}"
trap stop_local_audit_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
start_local_audit_server "{_bash_path(tmp_path)}" "{_bash_path(sys.executable)}" "{port}" "{_bash_path(tmp_path/'out')}" "{_bash_path(tmp_path/'err')}"
printf '%s' "$LOCAL_AUDIT_PID" > "{_bash_path(pid_file)}"
printf '%s' "$BASHPID" > "{_bash_path(shell_pid_file)}"
{ending}
''', encoding="utf-8", newline="\n")
    proc = subprocess.Popen([BASH, _bash_path(script)])
    pid, shell_pid = _wait_for_pid_files(proc, (pid_file, shell_pid_file))
    return proc, pid, shell_pid


def test_server_cleanup_on_success(tmp_path):
    proc, pid, _ = _run_case(tmp_path, "stop_local_audit_server")
    assert proc.wait(timeout=10) == 0
    assert not _alive(pid)


def test_server_cleanup_on_failure(tmp_path):
    proc, pid, _ = _run_case(tmp_path, "exit 7")
    assert proc.wait(timeout=10) == 7
    assert not _alive(pid)


def test_server_cleanup_on_termination(tmp_path):
    proc, pid, shell_pid = _run_case(tmp_path, "sleep 30 & wait $!")
    subprocess.run([BASH, "-c", f"kill -TERM {shell_pid}"], check=True)
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
source "{_bash_path(LIB)}"
trap stop_local_audit_server EXIT
start_local_audit_server "{_bash_path(tmp_path)}" "{_bash_path(sys.executable)}" "{port}" "{_bash_path(tmp_path/'new.out')}" "{_bash_path(tmp_path/'new.err')}"
''', encoding="utf-8", newline="\n")
        proc = subprocess.run([BASH, _bash_path(script)], timeout=10)
        assert proc.returncode != 0
        assert old.poll() is None
    finally:
        old.terminate()
        old.wait(timeout=10)
