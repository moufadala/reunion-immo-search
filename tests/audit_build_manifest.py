#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def fail(msg: str) -> int:
    print(f"BUILD_MANIFEST FAIL — {msg}")
    return 1


def main(argv: list[str]) -> int:
    app = Path(argv[1]) if len(argv) > 1 else Path("artifacts/app")
    path = app / "build_manifest.json"
    if not path.exists():
        return fail(f"missing {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return fail(f"invalid JSON: {exc}")
    required = ["schema_version", "generated_at", "git", "app_summary", "public"]
    missing = [k for k in required if k not in data]
    if missing:
        return fail(f"missing keys: {missing}")
    if data["schema_version"] != "immo_build_manifest_v1":
        return fail("unexpected schema_version")
    app_summary = data.get("app_summary") or {}
    if app_summary.get("listing_count", 0) <= 0:
        return fail("listing_count must be > 0")
    if not app_summary.get("listings_json_sha256"):
        return fail("missing listings_json_sha256")
    git = data.get("git") or {}
    if not git.get("commit_short"):
        return fail("missing git.commit_short")
    public = data.get("public") or {}
    if not str(public.get("canonical_url", "")).startswith("https://immo."):
        return fail("missing canonical public URL")
    print("BUILD_MANIFEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
