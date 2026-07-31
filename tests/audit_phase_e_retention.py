#!/usr/bin/env python3
"""Phase E P0 audit: retention safety + disk alert logic."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "artifact_retention.py"
FRESHNESS = ROOT / "freshness_check.py"
errors: list[str] = []

if not SCRIPT.exists():
    errors.append("missing scripts/artifact_retention.py")
if not FRESHNESS.exists():
    errors.append("missing freshness_check.py")
if errors:
    print("PHASE_E_RETENTION_AUDIT FAIL")
    for e in errors:
        print(" -", e)
    sys.exit(1)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if check and proc.returncode != 0:
        raise AssertionError(f"rc={proc.returncode} cmd={' '.join(cmd)}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    return proc


with tempfile.TemporaryDirectory() as td:
    art = Path(td) / "artifacts"
    art.mkdir()
    # Never-touch sentinels.
    for protected in ["app", ".git", "src", "vault", "vaults"]:
        p = art / protected
        p.mkdir()
        (p / "KEEP").write_text("keep", encoding="utf-8")
    (art / "state.db").write_text("keep", encoding="utf-8")
    # Retained/deleted candidates.
    for i in range(1, 7):
        for prefix in ["daily-clean-stage-", "daily-tech-stage-"]:
            p = art / f"{prefix}2026070{i}T000000Z"
            p.mkdir()
            (p / "marker.txt").write_text(str(i), encoding="utf-8")
    for i in range(1, 4):
        p = art / f"app.pre-promote-2026070{i}T000000Z"
        p.mkdir(); (p / "marker.txt").write_text(str(i), encoding="utf-8")
    (art / ".bak.old").write_text("delete", encoding="utf-8")

    dry = run([sys.executable, str(SCRIPT), "--artifacts", str(art)])
    dry_report = json.loads(dry.stdout)
    if dry_report["mode"] != "dry-run" or dry_report["delete_count"] != 13:
        errors.append(f"dry-run expected 13 delete candidates, got {dry_report.get('delete_count')}")
    if not all(not x["deleted"] for x in dry_report["delete_candidates"]):
        errors.append("dry-run must not delete")
    for protected in ["app/KEEP", ".git/KEEP", "src/KEEP", "vault/KEEP", "vaults/KEEP", "state.db"]:
        if not (art / protected).exists():
            errors.append(f"protected path disappeared during dry-run: {protected}")

    first = run([sys.executable, str(SCRIPT), "--artifacts", str(art), "--apply"])
    first_report = json.loads(first.stdout)
    second = run([sys.executable, str(SCRIPT), "--artifacts", str(art), "--apply"])
    second_report = json.loads(second.stdout)
    after = second_report["after"]
    if after["daily_clean_count"] > 1 or after["daily_tech_count"] > 1 or after["app_pre_promote_count"] > 1 or after["bak_count"] != 0:
        errors.append(f"retention bounds failed after two runs: {after}")
    if second_report["delete_count"] != 0:
        errors.append(f"second apply should be idempotent, got delete_count={second_report['delete_count']}")
    for protected in ["app/KEEP", ".git/KEEP", "src/KEEP", "vault/KEEP", "vaults/KEEP", "state.db"]:
        if not (art / protected).exists():
            errors.append(f"protected path deleted by apply: {protected}")

    # Disk simulation through freshness_check external alert logic: threshold set below actual usage so it must alert.
    disk = run([sys.executable, str(FRESHNESS), "--db", str(ROOT / "data" / "socle_p0.sqlite"), "--dry-run", "--artifact-size-threshold-gb", "0.001"], check=False)
    disk_report = json.loads(disk.stdout)
    if disk_report.get("status") != "down" or not any("disque immo" in a.get("message", "") for a in disk_report.get("alerts", [])):
        errors.append(f"disk threshold simulation expected disque immo DOWN, got {disk_report}")

print("PHASE_E_RETENTION_AUDIT", "PASS" if not errors else "FAIL")
if errors:
    for e in errors:
        print(" -", e)
    sys.exit(1)
print(json.dumps({"ok": True, "checks": ["dry-run lists", "protected guards", "two apply idempotent", "disk alert simulation"]}, ensure_ascii=False, indent=2))
