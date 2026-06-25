#!/usr/bin/env python3
"""Regression audit for listing_history.py using temporary JSON + SQLite DB."""
from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "listing_history.py"
SOURCE = ROOT / "artifacts" / "app" / "listings.json"
errors: list[str] = []

if not SCRIPT.exists():
    errors.append("missing src/listing_history.py")
if not SOURCE.exists():
    errors.append("missing artifacts/app/listings.json; run src/build_app.py first")
if errors:
    print("LISTING_HISTORY_AUDIT FAIL", errors)
    sys.exit(1)

base = json.loads(SOURCE.read_text(encoding="utf-8"))
items = [x for x in (base.get("listings") or []) if x.get("id") and (x.get("rent_eur") or x.get("price"))]
if len(items) < 3:
    print("LISTING_HISTORY_AUDIT FAIL not enough priced listings")
    sys.exit(1)

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    db = tmp / "history.sqlite"
    src1 = tmp / "listings1.json"
    src2 = tmp / "listings2.json"
    src3 = tmp / "listings3.json"

    sample = {"meta": base.get("meta", {}), "listings": copy.deepcopy(items[:5])}
    src1.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")

    r1 = subprocess.run([sys.executable, str(SCRIPT), "--source", str(src1), "--db", str(db), "--snapshot-at", "2026-06-24T00:00:00+00:00"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if r1.returncode != 0:
        errors.append(f"first snapshot failed rc={r1.returncode} stderr={r1.stderr[:300]}")
        rep1 = {"counts": {}}
    else:
        rep1 = json.loads(r1.stdout)
        if rep1["counts"].get("new") != 5:
            errors.append(f"first snapshot expected 5 new, got {rep1['counts'].get('new')}")

    changed = copy.deepcopy(sample)
    first = changed["listings"][0]
    old_price = first.get("rent_eur", first.get("price")) or 1000
    if "rent_eur" in first:
        first["rent_eur"] = int(old_price) - 50
    else:
        first["price"] = int(old_price) - 50
    # remove one listing to trigger disappearance
    changed["listings"] = changed["listings"][:-1]
    src2.write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8")
    r2 = subprocess.run([sys.executable, str(SCRIPT), "--source", str(src2), "--db", str(db), "--snapshot-at", "2026-06-25T00:00:00+00:00"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if r2.returncode != 0:
        errors.append(f"second snapshot failed rc={r2.returncode} stderr={r2.stderr[:300]}")
        rep2 = {"counts": {}}
    else:
        rep2 = json.loads(r2.stdout)
        if rep2["counts"].get("price_changed") != 1:
            errors.append(f"expected 1 price_changed, got {rep2['counts'].get('price_changed')}")
        if rep2["counts"].get("disappeared") != 1:
            errors.append(f"expected 1 disappeared, got {rep2['counts'].get('disappeared')}")

    # Restore full sample to trigger one reappearance.
    src3.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    r3 = subprocess.run([sys.executable, str(SCRIPT), "--source", str(src3), "--db", str(db), "--snapshot-at", "2026-06-26T00:00:00+00:00"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if r3.returncode != 0:
        errors.append(f"third snapshot failed rc={r3.returncode} stderr={r3.stderr[:300]}")
        rep3 = {"counts": {}}
    else:
        rep3 = json.loads(r3.stdout)
        if rep3["counts"].get("reappeared") != 1:
            errors.append(f"expected 1 reappeared, got {rep3['counts'].get('reappeared')}")

    con = sqlite3.connect(str(db))
    event_counts = dict(con.execute("SELECT event_type, COUNT(*) FROM listing_events GROUP BY event_type").fetchall())
    current_count = con.execute("SELECT COUNT(*) FROM listing_current").fetchone()[0]
    con.close()
    for typ in ["new", "price_changed", "disappeared", "reappeared"]:
        if event_counts.get(typ, 0) <= 0:
            errors.append(f"missing event type {typ} in sqlite audit")
    if current_count != 5:
        errors.append(f"expected 5 current rows, got {current_count}")

print("LISTING_HISTORY_AUDIT", "PASS" if not errors else "FAIL")
if errors:
    for e in errors:
        print(" -", e)
    sys.exit(1)
else:
    print(json.dumps({"event_counts": event_counts, "current_count": current_count}, ensure_ascii=False, indent=2))
