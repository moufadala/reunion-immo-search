from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reunion_watch_daily_notify.sh"


def _bash() -> str | None:
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if git_bash.exists():
        return str(git_bash)
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required for notifier runtime tests")


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _fixture(
    tmp_path: Path,
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    post_refresh_exit: int = 0,
    post_refresh_stdout: str = "",
    post_refresh_stderr: str = "",
) -> tuple[Path, Path]:
    run_dir = tmp_path / "async" / "20260818T120000Z"
    run_dir.mkdir(parents=True)
    stdout_file = run_dir / "worker.out"
    stderr_file = run_dir / "worker.err"
    stdout_file.write_text(stdout, encoding="utf-8")
    stderr_file.write_text(stderr, encoding="utf-8")
    (run_dir / "run.status").write_text(
        "\n".join(
            (
                "state=finished",
                "stamp=20260818T120000Z",
                "started_at=2026-08-18T12:00:00Z",
                f"stdout_file={stdout_file.as_posix()}",
                f"stderr_file={stderr_file.as_posix()}",
                f"exit_code={exit_code}",
                "",
            )
        ),
        encoding="utf-8",
        newline="\n",
    )
    post_refresh = tmp_path / "post-refresh.sh"
    _write_executable(
        post_refresh,
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' {post_refresh_stdout!r}\n"
        f"printf '%s\\n' {post_refresh_stderr!r} >&2\n"
        f"exit {post_refresh_exit}\n",
    )
    return run_dir, post_refresh


def _stale_fixture(tmp_path: Path) -> tuple[Path, Path]:
    run_dir = tmp_path / "async" / "20260818T120000Z"
    run_dir.mkdir(parents=True)
    (run_dir / "run.status").write_text(
        "\n".join(
            (
                "state=running",
                "stamp=20260818T120000Z",
                "started_at=2020-01-01T00:00:00Z",
                "stdout_file=",
                "stderr_file=",
                "exit_code=",
                "",
            )
        ),
        encoding="utf-8",
        newline="\n",
    )
    post_refresh = tmp_path / "post-refresh.sh"
    _write_executable(post_refresh, "#!/usr/bin/env bash\nexit 0\n")
    return run_dir, post_refresh


def _sink(tmp_path: Path) -> tuple[Path, Path]:
    sink_file = tmp_path / "notifications.log"
    sink = tmp_path / "notification-sink.sh"
    _write_executable(
        sink,
        """#!/usr/bin/env bash
set -euo pipefail
payload=$(cat)
printf '%s\n' "$payload" >> "$REUNION_WATCH_TEST_SINK_FILE"
if [ "${REUNION_WATCH_TEST_SINK_FAIL:-0}" = "1" ]; then
  echo "fixture sink refused notification" >&2
  exit 23
fi
""",
    )
    return sink, sink_file


def _run(tmp_path: Path, post_refresh: Path, sink: Path, sink_file: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "REUNION_WATCH_ASYNC_DIR": (tmp_path / "async").as_posix(),
            "REUNION_WATCH_POST_REFRESH_ALERTS": post_refresh.as_posix(),
            "REUNION_WATCH_NOTIFY_SINK": sink.as_posix(),
            "REUNION_WATCH_TEST_SINK_FILE": sink_file.as_posix(),
        }
    )
    env.update(extra_env)
    return subprocess.run(
        [str(BASH), SCRIPT.as_posix()],
        cwd=ROOT,
        env=env,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_script_has_valid_bash_syntax():
    result = subprocess.run(
        [str(BASH), "-n", SCRIPT.as_posix()],
        cwd=ROOT,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_failed_run_is_delivered_to_injectable_sink_with_actionable_evidence(tmp_path: Path):
    run_dir, post_refresh = _fixture(tmp_path, exit_code=17, stderr="collector exploded\n")
    sink, sink_file = _sink(tmp_path)

    result = _run(tmp_path, post_refresh, sink, sink_file)

    assert result.returncode == 0, result.stderr
    alert = sink_file.read_text(encoding="utf-8")
    assert "ALERTE RUN Watch quotidien: échec worker async" in alert
    assert "exit_code=17" in alert
    assert "20260818T120000Z/run.status" in alert.replace("\\", "/")
    assert "collector exploded" in alert
    assert (run_dir / "reported.ok").exists()


def test_successful_run_delivers_worker_and_search_alert_payload_before_ack(tmp_path: Path):
    run_dir, post_refresh = _fixture(
        tmp_path,
        exit_code=0,
        stdout="refresh ok\n",
        post_refresh_stdout="Nouvelle annonce correspondant à la recherche\n",
    )
    sink, sink_file = _sink(tmp_path)

    result = _run(tmp_path, post_refresh, sink, sink_file)

    assert result.returncode == 0, result.stderr
    delivered = sink_file.read_text(encoding="utf-8")
    assert "refresh ok" in delivered
    assert "Alertes immo post-refresh:" in delivered
    assert "Nouvelle annonce correspondant à la recherche" in delivered
    assert (run_dir / "reported.ok").exists()


def test_success_delivery_failure_returns_75_and_retry_delivers_before_ack(tmp_path: Path):
    run_dir, post_refresh = _fixture(
        tmp_path,
        exit_code=0,
        stdout="refresh ok\n",
        post_refresh_stdout="Recherche: appartement T3\n",
    )
    sink, sink_file = _sink(tmp_path)

    failed = _run(
        tmp_path,
        post_refresh,
        sink,
        sink_file,
        REUNION_WATCH_TEST_SINK_FAIL="1",
    )

    assert failed.returncode == 75
    assert not (run_dir / "reported.ok").exists()

    retry = _run(tmp_path, post_refresh, sink, sink_file)
    assert retry.returncode == 0, retry.stderr
    assert (run_dir / "reported.ok").exists()
    assert sink_file.read_text(encoding="utf-8").count("Recherche: appartement T3") == 2


def test_post_refresh_retryable_75_is_not_acknowledged_and_retries_success(tmp_path: Path):
    run_dir, post_refresh = _fixture(tmp_path, exit_code=0, stdout="refresh ok\n")
    marker = tmp_path / "post-refresh-first-attempt"
    _write_executable(
        post_refresh,
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"if [ ! -e \"{marker.as_posix()}\" ]; then\n"
        f"  touch \"{marker.as_posix()}\"\n"
        "  echo 'post-refresh lock busy' >&2\n"
        "  exit 75\n"
        "fi\n"
        "echo 'Recherche livrée après relance'\n",
    )
    sink, sink_file = _sink(tmp_path)

    busy = _run(tmp_path, post_refresh, sink, sink_file)

    assert busy.returncode == 75
    assert not sink_file.exists()
    assert not (run_dir / "reported.ok").exists()

    retry = _run(tmp_path, post_refresh, sink, sink_file)
    assert retry.returncode == 0, retry.stderr
    assert "Recherche livrée après relance" in sink_file.read_text(encoding="utf-8")
    assert (run_dir / "reported.ok").exists()


def test_sink_failure_is_nonzero_visible_and_keeps_run_retryable(tmp_path: Path):
    run_dir, post_refresh = _fixture(tmp_path, exit_code=9, stderr="source gate failed\n")
    sink, sink_file = _sink(tmp_path)

    result = _run(
        tmp_path,
        post_refresh,
        sink,
        sink_file,
        REUNION_WATCH_TEST_SINK_FAIL="1",
    )

    assert result.returncode == 75
    assert "notification delivery failed" in result.stderr
    assert "fixture sink refused notification" in result.stderr
    assert not (run_dir / "reported.ok").exists()

    retry = _run(tmp_path, post_refresh, sink, sink_file)
    assert retry.returncode == 0, retry.stderr
    assert (run_dir / "reported.ok").exists()
    assert sink_file.read_text(encoding="utf-8").count("exit_code=9") == 2


def test_stdout_is_the_delivery_fallback_when_no_sink_is_configured(tmp_path: Path):
    run_dir, post_refresh = _fixture(tmp_path, exit_code=12, stderr="fallback proof\n")
    sink, sink_file = _sink(tmp_path)

    result = _run(tmp_path, post_refresh, sink, sink_file, REUNION_WATCH_NOTIFY_SINK="")

    assert result.returncode == 0, result.stderr
    assert "ALERTE RUN Watch quotidien: échec worker async" in result.stdout
    assert "fallback proof" in result.stdout
    assert not sink_file.exists()
    assert (run_dir / "reported.ok").exists()


def test_post_refresh_alert_failure_is_delivered_and_marked_only_after_delivery(tmp_path: Path):
    run_dir, post_refresh = _fixture(
        tmp_path,
        exit_code=0,
        stdout="refresh ok\n",
        post_refresh_exit=8,
        post_refresh_stderr="post refresh exploded",
    )
    sink, sink_file = _sink(tmp_path)

    result = _run(tmp_path, post_refresh, sink, sink_file)

    assert result.returncode == 0, result.stderr
    alert = sink_file.read_text(encoding="utf-8")
    assert "post-refresh" in alert
    assert "post refresh exploded" in alert
    assert (run_dir / "reported.ok").exists()


def test_post_refresh_delivery_failure_returns_75_and_keeps_run_retryable(tmp_path: Path):
    run_dir, post_refresh = _fixture(
        tmp_path,
        exit_code=0,
        post_refresh_exit=8,
        post_refresh_stderr="post refresh exploded",
    )
    sink, sink_file = _sink(tmp_path)

    result = _run(
        tmp_path,
        post_refresh,
        sink,
        sink_file,
        REUNION_WATCH_TEST_SINK_FAIL="1",
    )

    assert result.returncode == 75
    assert "notification delivery failed" in result.stderr
    assert not (run_dir / "reported.ok").exists()


def test_stale_worker_delivery_failure_returns_75_without_stale_marker(tmp_path: Path):
    run_dir, post_refresh = _stale_fixture(tmp_path)
    sink, sink_file = _sink(tmp_path)

    result = _run(
        tmp_path,
        post_refresh,
        sink,
        sink_file,
        REUNION_WATCH_TEST_SINK_FAIL="1",
    )

    assert result.returncode == 75
    assert "notification delivery failed" in result.stderr
    assert "toujours running" in sink_file.read_text(encoding="utf-8")
    assert not (run_dir / "reported-stale.ok").exists()


def test_stale_worker_is_marked_only_after_successful_delivery(tmp_path: Path):
    run_dir, post_refresh = _stale_fixture(tmp_path)
    sink, sink_file = _sink(tmp_path)

    result = _run(tmp_path, post_refresh, sink, sink_file)

    assert result.returncode == 0, result.stderr
    assert "toujours running" in sink_file.read_text(encoding="utf-8")
    assert (run_dir / "reported-stale.ok").exists()
    assert not (run_dir / "reported.ok").exists()
