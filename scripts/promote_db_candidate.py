#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(path))


def integrity(path: Path) -> str:
    with closing(connect(path)) as con:
        row = con.execute("PRAGMA integrity_check").fetchone()
    return str(row[0] if row else "missing")


def source_column(con: sqlite3.Connection) -> str:
    cols = {row[1] for row in con.execute("PRAGMA table_info(rental_listings)")}
    if "source" in cols:
        return "source"
    if "source_site" in cols:
        return "source_site"
    raise SystemExit("candidate DB invalid: rental_listings lacks source/source_site column")


def active_counts(path: Path) -> dict[str, int]:
    with closing(connect(path)) as con:
        src_col = source_column(con)
        total = con.execute("SELECT COUNT(*) FROM rental_listings WHERE is_active=1").fetchone()[0]
        rows = con.execute(
            f"""
            SELECT COALESCE({src_col},'unknown') AS source_name, COUNT(*)
            FROM rental_listings
            WHERE is_active=1
            GROUP BY COALESCE({src_col},'unknown')
            ORDER BY COUNT(*) DESC, source_name
            """
        ).fetchall()
    return {"__total__": int(total), **{str(k): int(v) for k, v in rows}}


def validate_schema(path: Path) -> None:
    with closing(connect(path)) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"rental_listings"}
    missing = sorted(required - tables)
    if missing:
        raise SystemExit(f"candidate DB invalid: missing tables {missing}")


def pct_drop(before: int, after: int) -> float:
    if before <= 0:
        return 0.0 if after >= 0 else 100.0
    return max(0.0, (before - after) / before * 100.0)


def copy_sqlite(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    src_con = sqlite3.connect(str(src))
    dst_con = sqlite3.connect(str(dst))
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote an immo stage SQLite DB after integrity and source-volume guards.")
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--max-drop-pct", type=float, default=15.0)
    ap.add_argument("--max-critical-drop-pct", type=float, default=35.0)
    ap.add_argument("--critical-source", action="append", default=["seloger"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.candidate.is_file():
        raise SystemExit(f"candidate DB missing: {args.candidate}")
    if not args.target.is_file():
        raise SystemExit(f"target DB missing: {args.target}")

    validate_schema(args.candidate)
    cand_integrity = integrity(args.candidate)
    target_integrity = integrity(args.target)
    errors: list[str] = []
    if cand_integrity.lower() != "ok":
        errors.append(f"candidate integrity_check={cand_integrity}")
    if target_integrity.lower() != "ok":
        errors.append(f"target integrity_check={target_integrity}")

    before = active_counts(args.target)
    after = active_counts(args.candidate)
    total_drop = pct_drop(before.get("__total__", 0), after.get("__total__", 0))
    if total_drop > args.max_drop_pct:
        errors.append(
            f"total active rows dropped {total_drop:.1f}% "
            f"({before.get('__total__', 0)} -> {after.get('__total__', 0)})"
        )
    for src in args.critical_source:
        b = before.get(src, 0)
        a = after.get(src, 0)
        drop = pct_drop(b, a)
        if b > 0 and a <= 0:
            errors.append(f"critical source {src} disappeared ({b} -> {a})")
        elif drop > args.max_critical_drop_pct:
            errors.append(f"critical source {src} dropped {drop:.1f}% ({b} -> {a})")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = args.target.with_name(args.target.name + f".pre-promote-{stamp}")
    promoted = False

    if not errors and not args.dry_run:
        copy_sqlite(args.target, backup)
        # Build a temp copy first so a partial write cannot corrupt target.
        with TemporaryDirectory(prefix="immo-db-promote-") as td:
            tmp = Path(td) / args.target.name
            copy_sqlite(args.candidate, tmp)
            if integrity(tmp).lower() != "ok":
                errors.append("temporary promoted DB failed integrity_check")
            else:
                shutil.copy2(tmp, args.target)
                promoted = True

    payload = {
        "ok": not errors,
        "dry_run": args.dry_run,
        "promoted": promoted,
        "candidate": str(args.candidate),
        "target": str(args.target),
        "backup": str(backup) if promoted else None,
        "target_integrity": target_integrity,
        "candidate_integrity": cand_integrity,
        "before_counts": before,
        "after_counts": after,
        "total_drop_pct": round(total_drop, 3),
        "critical_sources": args.critical_source,
        "errors": errors,
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if errors:
        print("PROMOTE_DB_CANDIDATE FAIL", file=sys.stderr)
        return 2
    print("PROMOTE_DB_CANDIDATE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
