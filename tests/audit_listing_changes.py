#!/usr/bin/env python3
"""Regression audit for listing_changes.py using a temporary history DB."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "listing_changes.py"
errors: list[str] = []

if not SCRIPT.exists():
    errors.append("missing src/listing_changes.py")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    db = tmp / "history.sqlite"
    out = tmp / "changes.json"
    item = {
        "id": "src:1",
        "title": "Appartement test Moufia",
        "url": "https://example.test/1",
        "source_site": "test",
        "region": "Nord",
        "commune": "Saint-Denis",
        "primary_zone": "Moufia",
        "location_label": "Moufia · Saint-Denis",
        "rent_eur": 900,
        "surface_m2": 70,
        "rooms": 3,
        "bedrooms": 2,
        "image_url": "https://example.test/i.jpg",
    }
    con = sqlite3.connect(db)
    with con:
        con.executescript(
            """
            CREATE TABLE listing_current (id TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1, raw_json TEXT NOT NULL);
            CREATE TABLE listing_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_at TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                details_json TEXT
            );
            """
        )
        con.execute("INSERT INTO listing_current(id, active, raw_json) VALUES (?,?,?)", ("src:1", 1, json.dumps(item, ensure_ascii=False, sort_keys=True)))
        con.execute("INSERT INTO listing_events(listing_id,event_type,event_at,old_value,new_value,details_json) VALUES (?,?,?,?,?,?)", ("src:1", "new", "2026-01-01T00:00:00+00:00", None, "1000", "{}"))
        con.execute("INSERT INTO listing_events(listing_id,event_type,event_at,old_value,new_value,details_json) VALUES (?,?,?,?,?,?)", ("src:1", "price_changed", "2026-01-02T00:00:00+00:00", "1000", "900", "{}"))
        con.execute("INSERT INTO listing_events(listing_id,event_type,event_at,old_value,new_value,details_json) VALUES (?,?,?,?,?,?)", ("src:1", "disappeared", "2026-01-03T00:00:00+00:00", "1", "0", "{}"))
    con.close()

    html_out = tmp / "changes.html"
    run = subprocess.run([sys.executable, str(SCRIPT), "--db", str(db), "--out", str(out), "--html-out", str(html_out), "--limit", "10"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if run.returncode != 0:
        errors.append(f"listing_changes failed rc={run.returncode} stderr={run.stderr[:300]}")
    if not out.exists():
        errors.append("changes.json not written")
    else:
        payload = json.loads(out.read_text(encoding="utf-8"))
        if not payload.get("meta", {}).get("ok"):
            errors.append("payload meta.ok false")
        if payload.get("summary", {}).get("total_events") != 2:
            errors.append(f"expected 2 non-baseline changes, got {payload.get('summary')}")
        if payload.get("summary", {}).get("price_drops") != 1:
            errors.append("expected one price drop")
        types = [c.get("event_type") for c in payload.get("changes") or []]
        if "new" in types or "price_changed" not in types or "disappeared" not in types:
            errors.append(f"unexpected event types {types}")
        drop = next((c for c in payload.get("changes") or [] if c.get("event_type") == "price_changed"), {})
        if drop.get("delta_eur") != -100 or drop.get("direction") != "down":
            errors.append(f"bad price change classification {drop}")
        if not html_out.exists():
            errors.append("changes.html not written")
        else:
            html = html_out.read_text(encoding="utf-8")
            for needle in ["Journal des changements", "Baisse de prix", "1 000 € → 900 €", "Appartement test Moufia", "Baisses (1)", "Hausses (0)", 'data-direction="down"']:
                if needle not in html:
                    errors.append(f"changes.html missing {needle!r}")

print("LISTING_CHANGES_AUDIT", "PASS" if not errors else "FAIL")
if errors:
    for e in errors:
        print(" -", e)
    sys.exit(1)
