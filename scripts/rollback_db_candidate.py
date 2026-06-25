#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


def integrity(path: Path) -> str:
    con = sqlite3.connect(str(path))
    try:
        row = con.execute("PRAGMA integrity_check").fetchone()
        return str(row[0] if row else "missing")
    finally:
        con.close()


def active_count(path: Path) -> int:
    con = sqlite3.connect(str(path))
    try:
        return int(con.execute("SELECT COUNT(*) FROM rental_listings WHERE is_active=1").fetchone()[0])
    finally:
        con.close()


def validate(path: Path) -> dict[str, object]:
    missing = []
    if not path.is_file() or path.stat().st_size == 0:
        missing.append("db_file")
        return {"ok": False, "path": str(path), "missing": missing, "integrity": None, "active_rows": None}
    integ = integrity(path)
    rows = active_count(path) if integ.lower() == "ok" else None
    ok = integ.lower() == "ok" and rows is not None and rows >= 400
    return {"ok": ok, "path": str(path), "missing": missing, "integrity": integ, "active_rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="Drill or apply rollback of the immo production SQLite DB from a backup.")
    ap.add_argument("--backup", type=Path, required=True)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--qa-cmd", action="append", default=[])
    ap.add_argument("--apply", action="store_true", help="Actually replace --target. Default is non-destructive drill.")
    args = ap.parse_args()

    backup_check = validate(args.backup)
    if not backup_check["ok"]:
        payload = {"ok": False, "mode": "apply" if args.apply else "drill", "backup_check": backup_check, "errors": ["backup invalid"]}
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    qa_results = []
    restored_path = None
    target_snapshot = None
    mode = "apply" if args.apply else "drill"

    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target_snapshot = args.target.with_name(args.target.name + f".before-rollback-{stamp}")
        if args.target.exists():
            shutil.copy2(args.target, target_snapshot)
        shutil.copy2(args.backup, args.target)
        restored_path = args.target
        restored_check = validate(args.target)
        qa_env = None
    else:
        with TemporaryDirectory(prefix="immo-db-rollback-drill-") as td:
            restored_path = Path(td) / args.target.name
            shutil.copy2(args.backup, restored_path)
            restored_check = validate(restored_path)
            qa_env = {"IMMO_DB_PATH": str(restored_path)}
            for cmd in args.qa_cmd:
                proc = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=180, env={**dict(**__import__('os').environ), **qa_env})
                qa_results.append({
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout_tail": proc.stdout[-2000:],
                    "stderr_tail": proc.stderr[-2000:],
                })
            payload = {
                "ok": restored_check["ok"] and all(r["returncode"] == 0 for r in qa_results),
                "mode": mode,
                "backup": str(args.backup),
                "target": str(args.target),
                "target_snapshot": str(target_snapshot) if target_snapshot else None,
                "restored_path": str(restored_path),
                "backup_check": backup_check,
                "restored_check": restored_check,
                "qa_results": qa_results,
            }
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            print("ROLLBACK_DB_DRILL PASS" if payload["ok"] else "ROLLBACK_DB_DRILL FAIL")
            return 0 if payload["ok"] else 2

    # apply-mode QA runs against target after replacement
    for cmd in args.qa_cmd:
        proc = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=180)
        qa_results.append({
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
        })

    payload = {
        "ok": restored_check["ok"] and all(r["returncode"] == 0 for r in qa_results),
        "mode": mode,
        "backup": str(args.backup),
        "target": str(args.target),
        "target_snapshot": str(target_snapshot) if target_snapshot else None,
        "restored_path": str(restored_path),
        "backup_check": backup_check,
        "restored_check": restored_check,
        "qa_results": qa_results,
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("ROLLBACK_DB_APPLY PASS" if payload["ok"] else "ROLLBACK_DB_APPLY FAIL")
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
