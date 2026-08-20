from __future__ import annotations

import json
from datetime import datetime, timezone

from src import source_health


def test_missing_critical_portals_make_global_health_non_ok(tmp_path):
    payload = source_health.build_payload(
        tmp_path / "missing.db",
        reference_time=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
    )

    assert payload["ok"] is False
    assert payload["summary"]["stale_or_attention_critical"]


def test_cli_summary_reports_payload_truth(monkeypatch, tmp_path, capsys):
    payload = {
        "ok": False,
        "summary": {"stale_or_attention_critical": ["leboncoin"]},
        "sources": [],
        "generated_at": "2026-08-18T08:00:00+00:00",
    }
    monkeypatch.setattr(source_health, "build_payload", lambda *args, **kwargs: payload)
    monkeypatch.setattr(source_health, "render_html", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "sys.argv",
        [
            "source_health.py",
            "--db", str(tmp_path / "db.sqlite"),
            "--out", str(tmp_path / "health.json"),
            "--html-out", str(tmp_path / "health.html"),
        ],
    )

    assert source_health.main() == 0  # diagnostic; the manifest gate owns publication
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is False
