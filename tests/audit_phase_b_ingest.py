#!/usr/bin/env python3
"""Phase B P0 audit: SQLite WAL ingest, native-ID dedup, idempotence, core fields."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ingest.py"
SOURCE = ROOT / "artifacts" / "app" / "listings.json"
errors: list[str] = []

if not SCRIPT.exists():
    errors.append("missing ingest.py")
if not SOURCE.exists():
    errors.append("missing artifacts/app/listings.json")
if errors:
    print("PHASE_B_INGEST_AUDIT FAIL")
    for e in errors:
        print(" -", e)
    sys.exit(1)


def run_ingest(src: Path, db: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--source", str(src), "--db", str(db)],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"ingest failed rc={proc.returncode} stderr={proc.stderr[:500]}")
    return json.loads(proc.stdout)


def table_count(con: sqlite3.Connection, table: str) -> int:
    return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    db = tmp / "phase_b.sqlite"

    sample = {
        "listings": [
            {
                "source": "alpha",
                "source_id": "A-1",
                "id": "alpha:A-1",
                "city": "Saint-Denis",
                "price": 890,
                "surface": 42.5,
                "image_url": "https://example.invalid/a.jpg",
                "local_image_urls": [],
                "title": "T2 test",
                "url": "https://example.invalid/a",
            },
            {
                "source": "alpha",
                "source_id": "A-2",
                "id": "alpha:A-2",
                "city": "Saint-Denis",  # Same commune must not dedup without same native ID.
                "price": "900 €",
                "surface": "45 m²",
                "image_url": "data:image/png;base64,iVBORw0KGgo=",
                "title": "T2 same commune",
                "url": "https://example.invalid/b",
            },
            {
                "source": "beta",
                "id": "beta-native-1",  # fallback site_id when source_id is absent.
                "commune": "Sainte-Marie",
                "prix": 1200,
                "surface_m2": 65,
                "photos_phash": ["0123456789abcdef"],
                "title": "T3 phash",
            },
            {
                "source": "alpha",
                "source_id": "A-1",  # duplicate native ID updates row, not count.
                "id": "alpha:A-1-duplicate-display-id",
                "city": "Saint-Denis",
                "price": 875,
                "surface": 42.5,
                "photos_phash": ["abcdef0123456789"],
                "title": "T2 test updated",
            },
            {
                "source": "missing-id",
                "city": "Saint-Pierre",
                "price": 700,
                "surface": 30,
            },
        ]
    }
    src = tmp / "sample.json"
    src.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

    first = run_ingest(src, db)
    second = run_ingest(src, db)

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    journal_mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    listing_count = table_count(con, "listings")
    photo_count = table_count(con, "listing_photos")
    alpha = con.execute("SELECT * FROM listings WHERE source=? AND site_id=?", ("alpha", "A-1")).fetchone()
    same_commune = con.execute("SELECT COUNT(*) FROM listings WHERE commune=?", ("Saint-Denis",)).fetchone()[0]
    phash_rows = [r[0] for r in con.execute("SELECT phash FROM listing_photos ORDER BY phash").fetchall()]
    con.close()

    if journal_mode.lower() != "wal":
        errors.append(f"expected WAL journal_mode, got {journal_mode}")
    if listing_count != 3:
        errors.append(f"expected 3 listings after native-ID dedup/skip, got {listing_count}")
    if same_commune != 2:
        errors.append(f"same commune rows collapsed; expected 2 Saint-Denis rows, got {same_commune}")
    if not alpha or int(alpha["prix"] or 0) != 875:
        errors.append("duplicate native ID did not update alpha/A-1 price to latest value")
    if not alpha or alpha["commune"] != "Saint-Denis" or float(alpha["surface"] or 0) != 42.5:
        errors.append("commune/prix/surface fields not stored correctly")
    if photo_count < 2 or "0123456789abcdef" not in phash_rows or "abcdef0123456789" not in phash_rows:
        errors.append(f"photos_phash not stored correctly; photo_count={photo_count} phashes={phash_rows}")
    if second.get("inserted") != 0:
        errors.append(f"rerun should insert 0 new listings, got inserted={second.get('inserted')}")
    if first.get("total_listings") != second.get("total_listings") or first.get("total_listings") != 3:
        errors.append(f"idempotent total mismatch first={first.get('total_listings')} second={second.get('total_listings')}")
    if first.get("skipped_missing_native_id", 0) < 1:
        errors.append("missing native ID row was not reported as skipped")

print("PHASE_B_INGEST_AUDIT", "PASS" if not errors else "FAIL")
if errors:
    for e in errors:
        print(" -", e)
    sys.exit(1)
print(json.dumps({"first": first, "second": second, "listing_count": listing_count, "photo_count": photo_count}, ensure_ascii=False, indent=2))
