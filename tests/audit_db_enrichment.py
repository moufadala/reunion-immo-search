#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import importlib.util
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("normalize_db", ROOT / "src" / "normalize_db.py")
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load normalize_db.py")
_normalize_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_normalize_db)
refresh = _normalize_db.refresh

DEFAULT_DB = Path(os.environ.get("IMMO_DB_PATH", "/opt/data/data/reunion_watch.db"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    db = args.db
    report = refresh(db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    errors: list[str] = []

    tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    views = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    if "listing_product_enrichment" not in tables:
        errors.append("missing listing_product_enrichment table")
    for view in ["rental_listings_product", "rental_listings_canonical_residential"]:
        if view not in views:
            errors.append(f"missing view {view}")

    active = con.execute("SELECT COUNT(*) c FROM rental_listings WHERE COALESCE(is_active,1)=1").fetchone()["c"]
    enrich = con.execute("SELECT COUNT(*) c FROM listing_product_enrichment").fetchone()["c"]
    if enrich != active:
        errors.append(f"enrichment count mismatch: {enrich}!={active}")

    missing_city = con.execute(
        "SELECT COUNT(*) c FROM listing_product_enrichment WHERE city_normalized='Non précisée'"
    ).fetchone()["c"]
    city_coverage = 100 * (active - missing_city) / max(active, 1)
    if city_coverage < 90:
        errors.append(f"city normalization coverage too low: {city_coverage:.1f}%")

    dup_groups = con.execute(
        "SELECT COUNT(*) c FROM (SELECT duplicate_key FROM listing_product_enrichment GROUP BY duplicate_key HAVING COUNT(*)>1)"
    ).fetchone()["c"]
    non_canonical = con.execute("SELECT COUNT(*) c FROM listing_product_enrichment WHERE is_canonical=0").fetchone()["c"]
    if dup_groups <= 0 or non_canonical <= 0:
        errors.append(f"duplicate canonicalization ineffective: groups={dup_groups} non_canonical={non_canonical}")

    canon_bad = con.execute(
        """
        SELECT COUNT(*) c
        FROM listing_product_enrichment e
        LEFT JOIN listing_product_enrichment c
          ON c.source_site=e.canonical_source_site AND c.source_id=e.canonical_source_id
        WHERE c.source_site IS NULL
        """
    ).fetchone()["c"]
    if canon_bad:
        errors.append(f"broken canonical pointers: {canon_bad}")

    view_count = con.execute("SELECT COUNT(*) c FROM rental_listings_product WHERE COALESCE(is_active,1)=1").fetchone()["c"]
    canonical_residential = con.execute("SELECT COUNT(*) c FROM rental_listings_canonical_residential").fetchone()["c"]
    if view_count != active:
        errors.append(f"product view active count mismatch: {view_count}!={active}")
    if canonical_residential <= 0:
        errors.append("canonical residential view empty")

    region_counts = Counter(
        r["region"] for r in con.execute("SELECT region FROM listing_product_enrichment")
    )
    missing_fields_rows = con.execute(
        "SELECT missing_fields_json FROM listing_product_enrichment WHERE missing_fields_json!='[]' LIMIT 3"
    ).fetchall()
    for row in missing_fields_rows:
        json.loads(row["missing_fields_json"])

    print("DB_ENRICHMENT_AUDIT", "PASS" if not errors else "FAIL")
    print(json.dumps({
        "report": report,
        "active": active,
        "enrichment_rows": enrich,
        "city_coverage_pct": round(city_coverage, 1),
        "duplicate_groups": dup_groups,
        "non_canonical_items": non_canonical,
        "canonical_residential": canonical_residential,
        "region_counts": dict(region_counts),
    }, ensure_ascii=False, indent=2))
    if errors:
        for error in errors:
            print("-", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
