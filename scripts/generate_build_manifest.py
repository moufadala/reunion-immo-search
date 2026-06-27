#!/usr/bin/env python3
"""Generate a public build manifest for Immo Réunion artifacts.

This script is intentionally local and deterministic: it reads the app directory,
run-step statuses, git metadata, and optional promotion reports, then writes one
JSON file explaining what produced the public export.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_value(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"_parse_error": str(path)}
    return data if isinstance(data, dict) else {"value": data}


def step_statuses(run_dir: Path | None) -> list[dict[str, Any]]:
    if not run_dir or not run_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for status in sorted(run_dir.glob("*.status")):
        text = status.read_text(encoding="utf-8", errors="replace").strip()
        parts = dict(part.split("=", 1) for part in text.split() if "=" in part)
        out.append({"name": status.stem, "raw": text, **parts})
    return out


def summarize_app(app: Path) -> dict[str, Any]:
    listings_path = app / "listings.json"
    listings_data = load_json(listings_path)
    rows = listings_data.get("listings") or []
    if not isinstance(rows, list):
        rows = []
    source_counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or row.get("source_site") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
    health = load_json(app / "source_health.json")
    dedup = load_json(app / "dedup_groups.json")
    groups = dedup.get("groups") if isinstance(dedup.get("groups"), list) else dedup.get("dedup_groups")
    return {
        "app": str(app),
        "listings_json_sha256": sha256_file(listings_path),
        "index_html_sha256": sha256_file(app / "index.html"),
        "generated_at_from_payload": listings_data.get("generated_at"),
        "listing_count": len(rows),
        "visible_listing_count": sum(1 for row in rows if not isinstance(row, dict) or row.get("display_canonical") is not False),
        "local_primary_photos": sum(1 for row in rows if isinstance(row, dict) and row.get("local_image_url")),
        "local_multi_galleries": sum(1 for row in rows if isinstance(row, dict) and isinstance(row.get("local_image_urls"), list) and len(row["local_image_urls"]) > 1),
        "source_counts": dict(sorted(source_counts.items())),
        "source_health_summary": health.get("summary") or health.get("public") or {},
        "dedup_group_count": len(groups) if isinstance(groups, list) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Immo public build_manifest.json")
    ap.add_argument("--app", type=Path, default=ROOT / "artifacts" / "app")
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    app = args.app
    out = args.out or (app / "build_manifest.json")
    dirty = git_value(["status", "--short"])
    manifest: dict[str, Any] = {
        "schema_version": "immo_build_manifest_v1",
        "generated_at": utc_now(),
        "project": str(ROOT),
        "git": {
            "branch": git_value(["branch", "--show-current"]),
            "commit": git_value(["rev-parse", "HEAD"]),
            "commit_short": git_value(["rev-parse", "--short", "HEAD"]),
            "dirty": bool(dirty),
            "status_short": dirty.splitlines(),
        },
        "app_summary": summarize_app(app),
        "run_dir": str(args.run_dir) if args.run_dir else None,
        "run_steps": step_statuses(args.run_dir),
        "db": {"path": str(args.db) if args.db else None, "sha256": sha256_file(args.db) if args.db else None},
        "promotion_reports": {
            "promote_app": load_json(args.run_dir / "promote_app.json") if args.run_dir else {},
            "promote_db": load_json(args.run_dir / "promote_db.json") if args.run_dir else {},
            "rollback_app_drill": load_json(args.run_dir / "rollback_app_drill.json") if args.run_dir else {},
            "rollback_db_drill": load_json(args.run_dir / "rollback_db_drill.json") if args.run_dir else {},
        },
        "public": {
            "canonical_url": "https://immo.148.230.103.174.sslip.io/",
            "hostinger_compat_url": "https://immo.srv1723523.hstgr.cloud/",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out), "listing_count": manifest["app_summary"]["listing_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
