#!/usr/bin/env python3
"""Smoke test for source_health.py.

Uses relative timestamps so the test never expires as wall-clock time advances.
The --reference-time argument is injected to decouple the classification logic
from datetime.now(), making results deterministic regardless of when the test runs.
"""
from __future__ import annotations
import json, sqlite3, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "source_health.py"

REFERENCE_NOW = datetime.now(timezone.utc)

def ts(hours_ago: float) -> str:
    """ISO-8601 timestamp N hours before the injected reference time."""
    return (REFERENCE_NOW - timedelta(hours=hours_ago)).isoformat()


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    db = tmp / "watch.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE rental_listings("
        "source_site TEXT, is_active INTEGER, image_url TEXT, seen_last_at TEXT, fetched_at TEXT)"
    )
    # zimo: 48h ago → aging (36 < 48 ≤ 96)
    # seloger: 200h ago → stale + high severity
    # ofim_rss: inactive (is_active=0) → empty
    con.executemany(
        "INSERT INTO rental_listings VALUES (?,?,?,?,?)",
        [
            ("zimo",     1, "https://img", ts(48),  ts(48)),
            ("seloger",  1, "https://img", ts(200), ts(200)),
            ("ofim_rss", 0, None,          ts(48),  ts(48)),
        ],
    )
    con.commit()
    con.close()

    smoke = tmp / "SUMMARY.json"
    smoke.write_text(
        json.dumps({
            "generated_at": ts(1),
            "results": [{"id": "realestate_db_report", "summary": {"by_source": {"zimo": 1, "seloger": 1}}}],
        }),
        encoding="utf-8",
    )
    out  = tmp / "source_health.json"
    html = tmp / "source_health.html"

    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--db", str(db),
            "--smoke-summary", str(smoke),
            "--out", str(out),
            "--html-out", str(html),
            "--reference-time", REFERENCE_NOW.isoformat(),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is False

    by_source = {s["source"]: s for s in payload["sources"]}

    # A recent MAX timestamp may no longer hide weak listing-level coverage.
    assert by_source["zimo"]["status"] == "coverage-low", by_source["zimo"]
    assert by_source["zimo"]["active_coverage_ratio"] == 0.0
    assert by_source["zimo"]["coverage_threshold"] == 0.5
    assert by_source["seloger"]["status"] == "coverage-low", by_source["seloger"]
    assert by_source["seloger"]["active_coverage_ratio"] == 0.0
    assert "zimo" in payload["summary"]["coverage_below_threshold"]
    assert "seloger" in payload["summary"]["coverage_below_threshold"]
    assert by_source["seloger"]["severity"] == "high", by_source["seloger"]
    # ofim_rss: is_active=0 → empty
    assert by_source["ofim_rss"]["status"] == "empty", by_source["ofim_rss"]

    assert html.exists() and "Santé sources immo" in html.read_text(encoding="utf-8")

print("SOURCE_HEALTH_AUDIT PASS")
