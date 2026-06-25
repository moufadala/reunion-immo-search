#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "source_health.py"

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    db = tmp / "watch.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE rental_listings(source_site TEXT, is_active INTEGER, image_url TEXT, seen_last_at TEXT, fetched_at TEXT)")
    con.executemany(
        "INSERT INTO rental_listings VALUES (?,?,?,?,?)",
        [
            ("zimo", 1, "https://img", "2026-06-24T12:00:00+00:00", "2026-06-24T12:00:00+00:00"),
            ("seloger", 1, "https://img", "2026-06-10T12:00:00+00:00", "2026-06-10T12:00:00+00:00"),
            ("ofim_rss", 0, None, "2026-06-24T12:00:00+00:00", "2026-06-24T12:00:00+00:00"),
        ],
    )
    con.commit(); con.close()
    smoke = tmp / "SUMMARY.json"
    smoke.write_text(json.dumps({"generated_at":"2026-06-24T12:01:00+00:00","results":[{"id":"realestate_db_report","summary":{"by_source":{"zimo":1,"seloger":1}}}]}), encoding="utf-8")
    out = tmp / "source_health.json"
    html = tmp / "source_health.html"
    proc = subprocess.run([sys.executable, str(SCRIPT), "--db", str(db), "--smoke-summary", str(smoke), "--out", str(out), "--html-out", str(html)], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    by_source = {s["source"]: s for s in payload["sources"]}
    assert by_source["zimo"]["status"] in {"fresh", "aging"}
    assert by_source["seloger"]["status"] == "stale"
    assert by_source["seloger"]["severity"] == "high"
    assert html.exists() and "Santé sources immo" in html.read_text(encoding="utf-8")
print("SOURCE_HEALTH_AUDIT PASS")
