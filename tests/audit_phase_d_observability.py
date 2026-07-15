#!/usr/bin/env python3
"""Phase D P0 audit: runs freshness + D2bis failed Telegram notifications."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INGEST = ROOT / "ingest.py"
FRESHNESS = ROOT / "freshness_check.py"
errors: list[str] = []

if not FRESHNESS.exists():
    errors.append("missing freshness_check.py")
if not INGEST.exists():
    errors.append("missing ingest.py")
if errors:
    print("PHASE_D_OBSERVABILITY_AUDIT FAIL")
    for e in errors:
        print(" -", e)
    sys.exit(1)


def run(cmd: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed rc={proc.returncode}: {' '.join(cmd)}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    return proc


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    db = tmp / "socle.sqlite"
    source = tmp / "listings.json"
    source.write_text(json.dumps([
        {"source": "audit", "source_id": "A1", "title": "T2", "city": "Sainte-Marie", "price": 800, "surface": 55, "url": "https://example.invalid/a"}
    ], ensure_ascii=False), encoding="utf-8")

    good = run([sys.executable, str(INGEST), "--source", str(source), "--db", str(db)])
    good_report = json.loads(good.stdout)
    if good_report.get("run", {}).get("statut") != "ok":
        errors.append(f"good ingest expected run.statut=ok, got {good_report}")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "runs" not in tables:
        errors.append(f"missing runs table after ingest: {tables}")
    else:
        rr = con.execute("SELECT source, statut, n_rows, structure_hash FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if not rr or rr["statut"] != "ok" or rr["n_rows"] != 1 or not rr["structure_hash"]:
            errors.append(f"bad latest ok run row: {dict(rr) if rr else None}")
    con.close()

    ok = run([sys.executable, str(FRESHNESS), "--db", str(db), "--state", str(tmp / "state.json"), "--dry-run"])
    ok_report = json.loads(ok.stdout)
    if ok_report.get("status") != "ok" or ok_report.get("alerts"):
        errors.append(f"freshness expected ok/no alerts after good run, got {ok_report}")

    bad = run([sys.executable, str(INGEST), "--source", str(tmp / "missing.json"), "--db", str(db)], check=False)
    if bad.returncode == 0:
        errors.append("bad ingest with missing source should exit non-zero")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    br = con.execute("SELECT source, statut, n_rows FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    if not br or br["statut"] == "ok":
        errors.append(f"bad ingest expected latest runs.statut != ok, got {dict(br) if br else None}")

    bad_check = run([sys.executable, str(FRESHNESS), "--db", str(db), "--state", str(tmp / "state.json"), "--dry-run"], check=False)
    bad_report = json.loads(bad_check.stdout)
    if bad_report.get("status") != "down" or not any("source immo muette" in a.get("message", "") for a in bad_report.get("alerts", [])):
        errors.append(f"freshness expected down/source immo muette after bad latest run, got {bad_report}")

    # Restore OK should recover.
    restore = run([sys.executable, str(INGEST), "--source", str(source), "--db", str(db)])
    restore_report = json.loads(restore.stdout)
    if restore_report.get("run", {}).get("statut") != "ok":
        errors.append(f"restore ingest expected ok, got {restore_report}")
    restore_check = run([sys.executable, str(FRESHNESS), "--db", str(db), "--state", str(tmp / "state.json"), "--dry-run"])
    restore_status = json.loads(restore_check.stdout)
    if restore_status.get("status") != "ok" or restore_status.get("alerts"):
        errors.append(f"restore expected ok/no alerts, got {restore_status}")

    # D2bis: failed watch_notifications must alert.
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, criteria_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_checked_at TEXT);
    CREATE TABLE IF NOT EXISTS watch_notifications(id INTEGER PRIMARY KEY AUTOINCREMENT, watch_id INTEGER NOT NULL, listing_id INTEGER NOT NULL, notified_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', message_hash TEXT NOT NULL, error TEXT);
    """)
    con.execute("INSERT INTO watches(name, criteria_json, active, created_at, updated_at) VALUES ('audit failed send','{}',1,datetime('now'),datetime('now'))")
    wid = con.execute("SELECT id FROM watches WHERE name='audit failed send'").fetchone()[0]
    lid = con.execute("SELECT id FROM listings LIMIT 1").fetchone()[0]
    con.execute("INSERT INTO watch_notifications(watch_id, listing_id, notified_at, status, message_hash, error) VALUES (?,?,datetime('now'),'failed','audit','telegram down')", (wid, lid))
    con.commit(); con.close()
    failed_check = run([sys.executable, str(FRESHNESS), "--db", str(db), "--state", str(tmp / "state.json"), "--dry-run"], check=False)
    failed_report = json.loads(failed_check.stdout)
    if failed_report.get("status") != "down" or not any("watch_notifications.status='failed'" in a.get("message", "") for a in failed_report.get("alerts", [])):
        errors.append(f"D2bis expected down/failed notification alert, got {failed_report}")

print("PHASE_D_OBSERVABILITY_AUDIT", "PASS" if not errors else "FAIL")
if errors:
    for e in errors:
        print(" -", e)
    sys.exit(1)
print(json.dumps({"ok": True, "checks": ["runs", "source immo muette", "restore ok", "D2bis failed notifications"]}, ensure_ascii=False, indent=2))
