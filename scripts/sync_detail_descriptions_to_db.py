#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from enrich_source_details_v3 import clean_text, should_update  # noqa: E402

DEFAULT_DB = Path("/opt/data/data/reunion_watch.db")


def utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description="Synchronise les descriptions detail_full plus riches vers rental_listings.description, sans troncature.")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    db = Path(args.db)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    backup = None
    if not args.dry_run:
        backup = db.with_name(f"{db.name}.bak-sync-detail-desc-{utcstamp()}")
        shutil.copy2(db, backup)

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT r.source_site, r.source_id, r.title, r.description, d.description_full
        FROM rental_listings r
        JOIN listing_detail d ON d.source_site=r.source_site AND d.source_id=r.source_id
        WHERE COALESCE(r.is_active,1)=1
          AND length(COALESCE(d.description_full,'')) >= 80
        ORDER BY r.source_site, r.source_id
        """
    ).fetchall()

    counts = {"candidates": 0, "accepted": 0, "updated": 0, "rejected": 0}
    by_source: dict[str, dict[str, int]] = {}
    with report.open("w", encoding="utf-8") as log:
        for row in rows:
            src = row["source_site"]
            by_source.setdefault(src, {"candidates": 0, "accepted": 0, "updated": 0, "rejected": 0})
            counts["candidates"] += 1
            by_source[src]["candidates"] += 1
            ok, reason = should_update(row["description"], row["description_full"], row["title"])
            rec = {
                "source": src,
                "source_id": row["source_id"],
                "old_len": len(clean_text(row["description"])),
                "new_len": len(clean_text(row["description_full"])),
                "reason": reason,
                "dry_run": args.dry_run,
            }
            if ok:
                counts["accepted"] += 1
                by_source[src]["accepted"] += 1
                rec["action"] = "accept_dry_run" if args.dry_run else "update_db"
                if not args.dry_run:
                    con.execute(
                        "UPDATE rental_listings SET description=?, content_hash=NULL WHERE source_site=? AND source_id=?",
                        (clean_text(row["description_full"]), src, row["source_id"]),
                    )
                    counts["updated"] += 1
                    by_source[src]["updated"] += 1
            else:
                counts["rejected"] += 1
                by_source[src]["rejected"] += 1
                rec["action"] = "reject"
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if not args.dry_run:
        con.commit()
    con.close()

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "db": str(db),
        "backup": str(backup) if backup else None,
        "report": str(report),
        "counts": counts,
        "by_source": by_source,
    }
    summary_path = report.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
