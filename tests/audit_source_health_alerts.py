#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.source_health_alerts import build_alert, source_needs_attention


def source(name: str, status: str, severity: str, active_rows: int = 10) -> dict:
    return {
        "source": name,
        "status": status,
        "severity": severity,
        "reason": "fixture",
        "active_rows": active_rows,
        "smoke_status": "fixture",
        "is_critical": True,
    }


def test_new_attention_source_emits_message() -> None:
    payload = {"ok": True, "generated_at": "2026-06-27T00:00:00Z", "sources": [source("seloger", "stale", "high")]}
    message, state = build_alert(payload, {"version": 1, "sources": {}}, cooldown_hours=24, limit=8)
    assert source_needs_attention(payload["sources"][0]) is True
    assert "source(s) à vérifier" in message
    assert "seloger" in message
    assert state["sources"]["seloger"]["attention"] is True


def test_recovery_emits_message() -> None:
    payload = {"ok": True, "generated_at": "2026-06-27T00:00:00Z", "sources": [source("seloger", "fresh", "ok")]}
    old_state = {"version": 1, "sources": {"seloger": {"attention": True, "status_key": "stale|high|fixture"}}}
    message, state = build_alert(payload, old_state, cooldown_hours=24, limit=8)
    assert "revenues OK" in message
    assert state["sources"]["seloger"]["attention"] is False


def test_cli_dry_run_does_not_write_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        payload_path = tmp_path / "source_health.json"
        state_path = tmp_path / "state.json"
        payload_path.write_text(json.dumps({
            "ok": True,
            "generated_at": "2026-06-27T00:00:00Z",
            "sources": [source("zimo", "empty", "medium", active_rows=0)],
        }), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "src" / "source_health_alerts.py"), "--source-health", str(payload_path), "--state", str(state_path), "--dry-run"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        out = json.loads(result.stdout)
        assert out["ok"] is True
        assert out["dry_run"] is True
        assert out["would_emit"] is True
        assert "zimo" in out["message"]
        assert not state_path.exists()


def main() -> int:
    for test in [test_new_attention_source_emits_message, test_recovery_emits_message, test_cli_dry_run_does_not_write_state]:
        test()
    print("SOURCE_HEALTH_ALERTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
