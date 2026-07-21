#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from media_link_copy import copytree_media_aware

REQUIRED = ["index.html", "listings.json"]


def validate_app(path: Path) -> dict:
    missing = [name for name in REQUIRED if not (path / name).is_file() or (path / name).stat().st_size == 0]
    count = None
    if not missing:
        data = json.loads((path / "listings.json").read_text(encoding="utf-8"))
        rows = data.get("listings", data if isinstance(data, list) else [])
        count = len(rows) if isinstance(rows, list) else None
        if not count:
            missing.append("listings.json:empty_or_invalid")
    return {"ok": not missing, "path": str(path), "missing": missing, "listing_count": count}


def copy_tree(src: Path, dst: Path, *, media_mode: str) -> dict:
    if dst.exists():
        shutil.rmtree(dst)
    return copytree_media_aware(src, dst, media_mode=media_mode).to_dict()


def main() -> int:
    ap = argparse.ArgumentParser(description="Validated rollback drill/apply for the public immo static app.")
    ap.add_argument("--backup", type=Path, required=True, help="Backup app directory to restore")
    ap.add_argument("--target", type=Path, default=Path("artifacts/app"), help="Public app directory")
    ap.add_argument("--apply", action="store_true", help="Actually replace target. Default is a non-destructive temp drill.")
    ap.add_argument("--qa-cmd", action="append", default=[], help="Extra command to run after restore/drill, relative to repo root")
    ap.add_argument("--json-out", type=Path)
    ap.add_argument(
        "--media-copy-mode",
        choices=["copy", "hardlink"],
        default=os.environ.get("IMMO_MEDIA_COPY_MODE", "copy"),
        help="copy mode for media files during rollback restore/snapshots; default keeps legacy copy behavior",
    )
    args = ap.parse_args()

    if not args.backup.exists() or not args.backup.is_dir():
        raise SystemExit(f"backup dir missing: {args.backup}")
    backup_check = validate_app(args.backup)
    if not backup_check["ok"]:
        print(json.dumps(backup_check, ensure_ascii=False, indent=2))
        return 2

    root = Path(__file__).resolve().parents[1]
    restored_path: Path
    mode: str
    target_snapshot = None
    snapshot_stats = None
    restore_stats = None
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target_snapshot = args.target.with_name(args.target.name + f".pre-rollback-{stamp}")
        if args.target.exists():
            snapshot_stats = copytree_media_aware(args.target, target_snapshot, media_mode=args.media_copy_mode).to_dict()
        restore_stats = copy_tree(args.backup, args.target, media_mode=args.media_copy_mode)
        restored_path = args.target
        mode = "apply"
    else:
        tmp_obj = tempfile.TemporaryDirectory(prefix="immo-rollback-drill-")
        tmp = Path(tmp_obj.name)
        restored_path = tmp / "app"
        restore_stats = copy_tree(args.backup, restored_path, media_mode=args.media_copy_mode)
        mode = "drill"

    restored_check = validate_app(restored_path)
    qa_results = []
    env = {**dict(**__import__('os').environ), "IMMO_APP_PATH": str(restored_path), "PYTHONPYCACHEPREFIX": "/tmp/pycache-hermes"}
    for cmd in args.qa_cmd:
        proc = subprocess.run(cmd, cwd=root, shell=True, text=True, capture_output=True, env=env, timeout=120)
        qa_results.append({"cmd": cmd, "returncode": proc.returncode, "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-2000:]})
        if proc.returncode != 0:
            restored_check["ok"] = False

    payload = {
        "ok": bool(restored_check["ok"]),
        "mode": mode,
        "backup": str(args.backup),
        "target": str(args.target),
        "target_snapshot": str(target_snapshot) if target_snapshot else None,
        "media_copy_mode": args.media_copy_mode,
        "snapshot_copy_stats": snapshot_stats,
        "restore_copy_stats": restore_stats,
        "restored_path": str(restored_path),
        "backup_check": backup_check,
        "restored_check": restored_check,
        "qa_results": qa_results,
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("ROLLBACK_DRILL PASS" if payload["ok"] else "ROLLBACK_DRILL FAIL", file=sys.stderr if not payload["ok"] else sys.stdout)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
