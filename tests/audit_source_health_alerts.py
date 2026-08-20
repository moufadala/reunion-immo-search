#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.source_health_alerts import build_alert, source_needs_attention


def source(name: str, status: str, severity: str, active_rows: int = 10, age_hours: float = 100.0, last_seen_at: str = "2026-06-26T00:00:00+00:00") -> dict:
    return {
        "source": name,
        "status": status,
        "severity": severity,
        "reason": f"vu il y a {age_hours:.1f}h",
        "active_rows": active_rows,
        "last_seen_at": last_seen_at,
        "age_hours": age_hours,
        "smoke_status": "fixture",
        "is_critical": True,
    }


def test_new_attention_source_emits_5_line_message() -> None:
    payload = {"ok": True, "generated_at": "2026-06-27T00:00:00Z", "sources": [source("seloger", "stale", "high")]}
    message, state = build_alert(payload, {"version": 2, "sources": {}}, mode="changes", limit=8)
    assert source_needs_attention(payload["sources"][0]) is True
    assert "⚠️ IMMO · Seloger est à vérifier" in message
    assert "Depuis :" in message
    assert "Pourquoi :" in message
    assert "Qui agit :" in message
    assert "Preuve : sqlite3 /opt/data/data/reunion_watch.db" in message
    assert len([line for line in message.strip().splitlines() if line]) == 5
    assert state["sources"]["seloger"]["attention"] is True


def test_same_bad_state_is_silent_even_when_reason_age_changes() -> None:
    payload = {"ok": True, "generated_at": "2026-06-27T00:00:00Z", "sources": [source("seloger", "stale", "high", age_hours=101.0)]}
    old_state = {"version": 2, "sources": {"seloger": {"attention": True, "status_key": "stale|high", "first_attention_at": "2026-06-25T00:00:00+00:00"}}}
    message, state = build_alert(payload, old_state, mode="changes", limit=8)
    assert message == ""
    assert state["sources"]["seloger"]["attention"] is True


def test_recovery_emits_5_line_message() -> None:
    fresh_seen = datetime.now(timezone.utc).isoformat()
    payload = {"ok": True, "generated_at": "2026-06-27T00:00:00Z", "sources": [source("leboncoin", "fresh", "ok", active_rows=40, age_hours=1.0, last_seen_at=fresh_seen)]}
    old_state = {"version": 2, "sources": {"leboncoin": {"attention": True, "status_key": "stale|high", "first_attention_at": "2026-06-23T00:00:00+00:00"}}}
    message, state = build_alert(payload, old_state, mode="changes", limit=8)
    assert "🟢 IMMO · Leboncoin est revenue" in message
    assert "40 active(s)" in message
    assert "Pourquoi : leboncoin réalimente le feed public" in message
    assert "Qui agit : personne · résolu" in message
    assert len([line for line in message.strip().splitlines() if line]) == 5
    assert state["sources"]["leboncoin"]["attention"] is False


def test_status_ok_with_stale_measure_is_not_recovered() -> None:
    payload = {"ok": True, "generated_at": "2026-06-27T00:00:00Z", "sources": [source("leboncoin", "fresh", "ok", active_rows=366, age_hours=1.0, last_seen_at="2026-07-30T00:00:00+00:00")]}
    old_state = {"version": 2, "sources": {"leboncoin": {"attention": True, "status_key": "stale|high", "first_attention_at": "2026-08-03T06:44:00+00:00"}}}
    message, state = build_alert(payload, old_state, mode="changes", limit=8)
    assert "est revenue" not in message
    assert "⚠️ IMMO · Leboncoin est à vérifier" in message
    assert "statut annoncé OK, mais dernière donnée > 36 h" in message
    assert "Depuis :" in message and "j" in message
    assert state["sources"]["leboncoin"]["attention"] is True
    assert state["sources"]["leboncoin"]["first_attention_at"].startswith("2026-07-30")


def test_digest_once_per_day() -> None:
    payload = {"ok": True, "generated_at": "2026-06-27T00:00:00Z", "sources": [source("zimo", "empty", "medium", active_rows=0)]}
    state = {"version": 2, "sources": {"zimo": {"attention": True, "status_key": "empty|medium", "first_attention_at": "2026-06-25T00:00:00+00:00"}}}
    first, state = build_alert(payload, state, mode="digest", limit=8)
    second, state = build_alert(payload, state, mode="digest", limit=8)
    assert "🟡 IMMO · Zimo reste à vérifier" in first
    assert second == ""


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
            env={**os.environ, "PYTHONUTF8": "1"},
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        out = json.loads(result.stdout)
        assert out["ok"] is True
        assert out["dry_run"] is True
        assert out["would_emit"] is True
        assert "zimo" in out["message"].lower()
        assert not state_path.exists()


def main() -> int:
    for test in [
        test_new_attention_source_emits_5_line_message,
        test_same_bad_state_is_silent_even_when_reason_age_changes,
        test_recovery_emits_5_line_message,
        test_status_ok_with_stale_measure_is_not_recovered,
        test_digest_once_per_day,
        test_cli_dry_run_does_not_write_state,
    ]:
        test()
    print("SOURCE_HEALTH_ALERTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
