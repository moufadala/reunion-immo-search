#!/usr/bin/env python3
"""Audit the public build_manifest.json for integrity and traceability.

Strict rules (P0):
- git.dirty must be exactly False → dirty/unknown builds must not be promoted
- git.commit and git.commit_short must be coherent
- run_dir must be set      → manifest must be produced by a tracked daily run
- run_steps must not be empty and every step must have rc=0
- db.path and db.sha256 must be set when a DB path is part of the manifest
- listing_count > 0        → public artifact must have content
- listings_json_sha256 present → file hash must be captured
- canonical_url present    → deployment target must be explicit
"""
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

    # P0: git cleanliness — dirty or unknown builds must not be promoted.
    git = data.get("git") or {}
    if git.get("dirty") is not False:
        return fail(
            "git.dirty is not exactly false — build was produced from an uncommitted "
            "or unknown working tree; promote only from a clean commit"
        )
    commit = str(git.get("commit") or "")
    commit_short = str(git.get("commit_short") or "")
    if not commit_short:
        return fail("missing git.commit_short")
    if not commit:
        return fail("missing git.commit")
    if not commit.startswith(commit_short):
        return fail("git.commit_short does not match git.commit")

    # P0: run traceability — manifest must originate from a tracked daily run
    if not data.get("run_dir"):
        return fail(
            "run_dir is null — manifest was not produced by a tracked daily run; "
            "pass --run-dir to generate_build_manifest.py"
        )
    run_steps = data.get("run_steps")
    if not run_steps:
        return fail(
            "run_steps is empty — no pipeline steps were recorded; "
            "the build was not executed through the standard daily pipeline"
        )
    if not isinstance(run_steps, list):
        return fail("run_steps must be a list")
    bad_steps: list[str] = []
    for step in run_steps:
        if not isinstance(step, dict):
            bad_steps.append(repr(step))
            continue
        rc = str(step.get("rc", ""))
        if rc != "0":
            bad_steps.append(f"{step.get('name', '<unknown>')} rc={rc or '<missing>'}")
    if bad_steps:
        return fail(f"non-zero or missing run step rc: {bad_steps[:5]}")

    db = data.get("db") or {}
    if "db" in data and not (db.get("path") and db.get("sha256")):
        return fail("missing db.path or db.sha256")

    # Listing content
    app_summary = data.get("app_summary") or {}
    if app_summary.get("listing_count", 0) <= 0:
        return fail("listing_count must be > 0")
    if not app_summary.get("listings_json_sha256"):
        return fail("missing listings_json_sha256")

    # Deployment target
    public = data.get("public") or {}
    if not str(public.get("canonical_url", "")).startswith("https://immo."):
        return fail("missing canonical public URL")

    print("BUILD_MANIFEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
