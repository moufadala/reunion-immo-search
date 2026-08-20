from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "source_health_alerts.py"
POST_REFRESH_SCRIPT = ROOT / "scripts" / "reunion_watch_post_refresh_alerts.sh"


def _source(status: str) -> dict[str, object]:
    severity = {"coverage-low": "high", "stale": "high", "unknown": "medium"}[status]
    last_seen = None if status == "unknown" else datetime.now(timezone.utc).isoformat()
    if status == "stale":
        last_seen = "2020-01-01T00:00:00+00:00"
    return {
        "source": "leboncoin",
        "status": status,
        "severity": severity,
        "reason": f"fixture {status}",
        "active_rows": 20,
        "active_recent_rows": 4,
        "active_coverage_ratio": 0.2,
        "last_seen_at": last_seen,
        "age_hours": None,
        "is_critical": True,
    }


def _write_payload(path: Path, status: str) -> None:
    path.write_text(
        json.dumps(
            {
                "ok": False,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": {
                    "stale_or_attention_critical": ["leboncoin"] if status != "coverage-low" else [],
                    "coverage_below_threshold": ["leboncoin"] if status == "coverage-low" else [],
                },
                "sources": [_source(status)],
            }
        ),
        encoding="utf-8",
    )


def _run(
    payload: Path,
    state: Path,
    *,
    dry_run: bool = False,
    sink: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--source-health",
        str(payload),
        "--state",
        str(state),
    ]
    if dry_run:
        command.append("--dry-run")
    if sink:
        command.extend(["--sink", *sink])
    run_env = os.environ.copy()
    run_env["PYTHONUTF8"] = "1"
    run_env.update(env or {})
    return subprocess.run(
        command,
        cwd=ROOT,
        env=run_env,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("status", ["coverage-low", "stale", "unknown"])
def test_valid_unhealthy_payload_emits_actionable_dry_run(status: str, tmp_path: Path):
    payload = tmp_path / "source_health.json"
    state = tmp_path / "state.json"
    _write_payload(payload, status)

    result = _run(payload, state, dry_run=True)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["attention_count"] == 1
    assert report["would_emit"] is True
    assert "Leboncoin est à vérifier" in report["message"]
    assert not state.exists()


def _sink_script(path: Path) -> Path:
    script = path / "sink.py"
    script.write_text(
        """from pathlib import Path
import os
import sys

payload = sys.stdin.read()
with Path(os.environ["SOURCE_HEALTH_TEST_SINK_FILE"]).open("a", encoding="utf-8") as out:
    out.write(payload)
raise SystemExit(int(os.environ.get("SOURCE_HEALTH_TEST_SINK_EXIT", "0")))
""",
        encoding="utf-8",
    )
    return script


def test_transport_failure_returns_75_without_ack_and_retry_emits_again(tmp_path: Path):
    payload = tmp_path / "source_health.json"
    state = tmp_path / "state.json"
    sink_log = tmp_path / "sink.log"
    sink_script = _sink_script(tmp_path)
    _write_payload(payload, "stale")
    sink = [sys.executable, str(sink_script)]
    env = {
        "SOURCE_HEALTH_TEST_SINK_FILE": str(sink_log),
        "SOURCE_HEALTH_TEST_SINK_EXIT": "23",
    }

    failed = _run(payload, state, sink=sink, env=env)

    assert failed.returncode == 75
    assert "source health alert delivery failed" in failed.stderr
    assert not state.exists()

    env["SOURCE_HEALTH_TEST_SINK_EXIT"] = "0"
    retry = _run(payload, state, sink=sink, env=env)
    assert retry.returncode == 0, retry.stderr
    assert state.exists()
    assert sink_log.read_text(encoding="utf-8").count("Leboncoin est à vérifier") == 2


def test_post_refresh_wrapper_passes_the_acknowledged_sink_to_source_health_alerts():
    wrapper = (ROOT / "scripts" / "reunion_watch_post_refresh_alerts.sh").read_text(encoding="utf-8")

    assert "REUNION_WATCH_NOTIFY_SINK" in wrapper
    assert 'source_health_alerts.py --sink "$REUNION_WATCH_NOTIFY_SINK"' in wrapper


def test_post_refresh_wrapper_has_valid_bash_syntax():
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
    if not bash:
        pytest.skip("bash is required for wrapper syntax validation")
    result = subprocess.run(
        [bash, "-n", (ROOT / "scripts" / "reunion_watch_post_refresh_alerts.sh").as_posix()],
        cwd=ROOT,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_post_refresh_lock_busy_returns_retryable_75(tmp_path: Path):
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
    if not bash:
        pytest.skip("bash is required for wrapper runtime validation")
    fake_flock = tmp_path / "flock-busy.sh"
    fake_flock.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8", newline="\n")
    fake_flock.chmod(0o755)
    env = os.environ.copy()
    env["REUNION_WATCH_FLOCK_BIN"] = fake_flock.as_posix()

    result = subprocess.run(
        [bash, POST_REFRESH_SCRIPT.as_posix()],
        cwd=ROOT,
        env=env,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 75, result.stderr


def test_successful_ack_deduplicates_the_same_state(tmp_path: Path):
    payload = tmp_path / "source_health.json"
    state = tmp_path / "state.json"
    sink_log = tmp_path / "sink.log"
    sink_script = _sink_script(tmp_path)
    _write_payload(payload, "coverage-low")
    sink = [sys.executable, str(sink_script)]
    env = {"SOURCE_HEALTH_TEST_SINK_FILE": str(sink_log)}

    first = _run(payload, state, sink=sink, env=env)
    second = _run(payload, state, sink=sink, env=env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert sink_log.read_text(encoding="utf-8").count("Leboncoin est à vérifier") == 1
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["sources"]["leboncoin"]["attention"] is True
