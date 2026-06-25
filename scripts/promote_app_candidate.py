#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def validate_app(path: Path) -> None:
    for name in ("index.html", "listings.json"):
        p = path / name
        if not p.is_file() or p.stat().st_size == 0:
            raise SystemExit(f"candidate invalid: missing/non-empty {p}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote a generated immo static app only after volume/source guard passes.")
    ap.add_argument("--candidate", type=Path, required=True, help="Generated app directory to promote")
    ap.add_argument("--target", type=Path, default=Path("artifacts/app"), help="Published app directory")
    ap.add_argument("--max-drop-pct", type=float, default=15.0)
    ap.add_argument("--max-critical-drop-pct", type=float, default=35.0)
    ap.add_argument("--critical-source", action="append", default=["seloger"])
    ap.add_argument("--json-out", type=Path, default=Path("artifacts/app/promote_guard.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    validate_app(args.candidate)
    if not args.target.exists():
        raise SystemExit(f"target missing for baseline guard: {args.target}")

    guard_out = args.json_out.with_name(args.json_out.stem + ".delta.json")
    cmd = [
        sys.executable, str(root / "scripts" / "audit_public_delta_guard.py"),
        "--baseline", str(args.target),
        "--candidate", str(args.candidate),
        "--max-drop-pct", str(args.max_drop_pct),
        "--max-critical-drop-pct", str(args.max_critical_drop_pct),
        "--json-out", str(guard_out),
    ]
    for src in args.critical_source:
        cmd += ["--critical-source", src]
    proc = subprocess.run(cmd, cwd=root, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = args.target.with_name(args.target.name + f".pre-promote-{stamp}")
    promoted = False
    if not args.dry_run:
        shutil.copytree(args.target, backup)
        if args.target.exists():
            shutil.rmtree(args.target)
        shutil.copytree(args.candidate, args.target)
        # nginx container must be able to read after restrictive cron umasks
        for p in args.target.rglob("*"):
            if p.is_dir():
                p.chmod(0o755)
            else:
                p.chmod(0o644)
        args.target.chmod(0o755)
        promoted = True

    payload = {
        "ok": True,
        "dry_run": args.dry_run,
        "promoted": promoted,
        "candidate": str(args.candidate),
        "target": str(args.target),
        "backup": str(backup) if promoted else None,
        "guard_json": str(guard_out),
        "guard_stdout_tail": proc.stdout[-2000:],
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("PROMOTE_APP_CANDIDATE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
