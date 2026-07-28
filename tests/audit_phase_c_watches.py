#!/usr/bin/env python3
"""Phase C P0 audit: watches table + exactly-once alert per new matching listing."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "check_watches.py"
errors: list[str] = []

if not SCRIPT.exists():
    errors.append("missing check_watches.py")
if errors:
    print("PHASE_C_WATCHES_AUDIT FAIL")
    for e in errors:
        print(" -", e)
    sys.exit(1)


def run_check(db: Path, transport_log: Path, watch_name: str = "audit_watch") -> dict:
    # limit=1 intentionally catches the starvation bug where an already-notified
    # first match can hide a newer unnotified match behind it.
    # transaction/type catch the real-watch bug where those criteria were ignored silently.
    criteria = {"commune": "Saint-Denis", "transaction": "location", "type": ["appartement"], "prix_max": 900, "limit": 1}
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--ensure-schema",
            "--upsert-watch",
            watch_name,
            "--criteria-json",
            json.dumps(criteria, ensure_ascii=False),
            "--transport-log",
            str(transport_log),
        ],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"check_watches failed rc={proc.returncode} stderr={proc.stderr[:500]} stdout={proc.stdout[:500]}")
    return json.loads(proc.stdout)


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    db = tmp / "watch.sqlite"
    log = tmp / "telegram.log"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            site_id TEXT NOT NULL,
            title TEXT,
            url TEXT,
            commune TEXT,
            prix INTEGER,
            surface REAL,
            raw_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source, site_id)
        );
        """
    )
    rows = [
        # These two rows must NOT match. Before the matcher understood transaction/type,
        # one of them would pass silently and steal the limit=1 slot.
        ("alpha", "A-0", "Vente wrong transaction", "https://example.invalid/z", "Saint-Denis", 800, 52.0, {"transaction": "vente", "type": "Appartement"}),
        ("alpha", "A-0b", "Location wrong type", "https://example.invalid/y", "Saint-Denis", 800, 52.0, {"transaction": "location", "type": "Terrain"}),
        ("alpha", "A-1", "T2 audit match", "https://example.invalid/a", "Saint-Denis", 890, 42.0, {"transaction": "location", "type": "Appartement"}),
        ("alpha", "A-2", "T3 too expensive", "https://example.invalid/b", "Saint-Denis", 1200, 70.0, {"transaction": "location", "type": "Appartement"}),
        ("beta", "B-1", "Other commune", "https://example.invalid/c", "Sainte-Marie", 800, 50.0, {"transaction": "location", "type": "Appartement"}),
    ]
    for source, site_id, title, url, commune, prix, surface, extra in rows:
        raw_obj = {"source": source, "source_id": site_id, "title": title, **extra}
        raw = json.dumps(raw_obj, ensure_ascii=False)
        con.execute(
            """INSERT INTO listings(source,site_id,title,url,commune,prix,surface,raw_json,first_seen_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (source, site_id, title, url, commune, prix, surface, raw),
        )
    con.commit()
    con.close()

    first = run_check(db, log)
    second = run_check(db, log)

    con = sqlite3.connect(db)
    raw = json.dumps({"source": "alpha", "source_id": "A-3", "title": "T1 new behind old", "transaction": "location", "type": "Appartement"}, ensure_ascii=False)
    con.execute(
        """INSERT INTO listings(source,site_id,title,url,commune,prix,surface,raw_json,first_seen_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        ("alpha", "A-3", "T1 new behind old", "https://example.invalid/d", "Saint-Denis", 850, 28.0, raw),
    )
    con.commit()
    con.close()
    third = run_check(db, log)

    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    notif_count = con.execute("SELECT COUNT(*) FROM watch_notifications").fetchone()[0]
    watch_count = con.execute("SELECT COUNT(*) FROM watches").fetchone()[0]
    con.close()

    if "watches" not in tables or "watch_notifications" not in tables:
        errors.append(f"missing watch tables: {tables}")
    if first.get("alerts_sent") != 1:
        errors.append(f"first run expected 1 alert, got {first.get('alerts_sent')}: {first}")
    if second.get("alerts_sent") != 0:
        errors.append(f"second run expected 0 alerts, got {second.get('alerts_sent')}: {second}")
    if third.get("alerts_sent") != 1:
        errors.append(f"third run expected 1 alert for new match behind old notified row, got {third.get('alerts_sent')}: {third}")
    if len(lines) != 2:
        errors.append(f"transport log expected 2 messages, got {len(lines)}")
    if notif_count != 2:
        errors.append(f"watch_notifications expected 2 rows, got {notif_count}")
    if watch_count != 1:
        errors.append(f"watches expected 1 row, got {watch_count}")
    if lines and "T2 audit match" not in lines[0].get("text", ""):
        errors.append("alert text does not contain matching listing title")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    db = tmp / "commune_list.sqlite"
    log = tmp / "telegram.log"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            site_id TEXT NOT NULL,
            title TEXT,
            url TEXT,
            commune TEXT,
            prix INTEGER,
            surface REAL,
            raw_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source, site_id)
        );
        """
    )
    for site_id, commune in [("N1", "Saint-Denis"), ("N2", "Sainte-Marie"), ("N3", "Sainte-Suzanne"), ("E1", "Saint-André"), ("S1", "Saint-Pierre")]:
        raw = json.dumps({"transaction": "location", "type": "Appartement", "source_id": site_id}, ensure_ascii=False)
        con.execute(
            """INSERT INTO listings(source,site_id,title,url,commune,prix,surface,raw_json,first_seen_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            ("audit", site_id, f"Location {commune}", f"https://example.invalid/{site_id}", commune, 850, 55.0, raw),
        )
    con.commit(); con.close()
    criteria = {"communes": ["Saint-Denis", "Sainte-Marie", "Sainte-Suzanne", "Saint-André"], "transaction": "location", "type": ["appartement"], "prix_max": 1100, "surface_min": 50, "limit": 10}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--db", str(db), "--ensure-schema", "--upsert-watch", "nord_est", "--criteria-json", json.dumps(criteria, ensure_ascii=False), "--transport-log", str(log)], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if proc.returncode != 0:
        errors.append(f"commune-list run failed rc={proc.returncode}: {proc.stderr[:300]}")
    else:
        summary = json.loads(proc.stdout)
        if summary.get("matches") != 4 or summary.get("alerts_sent") != 4:
            errors.append(f"commune-list expected 4 Nord+Est matches, got {summary}")

print("PHASE_C_WATCHES_AUDIT", "PASS" if not errors else "FAIL")
if errors:
    for e in errors:
        print(" -", e)
    sys.exit(1)
print(json.dumps({"first": first, "second": second, "third": third, "transport_messages": len(lines), "notifications": notif_count}, ensure_ascii=False, indent=2))
