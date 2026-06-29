#!/usr/bin/env python3
"""Guardrail for the public GitHub repository.

The public repo must contain source, tests, docs, and explicit anonymized
fixtures only. Real generated portal outputs live on the VPS/runtime and must
not be tracked under artifacts/app.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\+?262|0)(?:[\s.\-/]*(?:262|692|693|6|7|2))?(?:[\s.\-/]*\d){7,9}(?![A-Za-z0-9])"
)
SECRET_HINT_RE = re.compile(
    r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=:-]{16,}"
)

FORBIDDEN_PREFIXES = (
    "artifacts/app/",
    "data/",
    "run/",
    "logs/",
    "cache/",
    "secrets/",
)
FORBIDDEN_SUFFIXES = (
    ".sqlite",
    ".sqlite3",
    ".db",
    ".db-wal",
    ".db-shm",
    ".pem",
    ".key",
)
ALLOWED_EXACT = {
    # GitHub's documented placeholder email is safe if it ever appears in docs.
    "noreply@github.com",
}


def git_ls_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [line for line in proc.stdout.splitlines() if line]


def is_text(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if b"\0" in data:
        return False
    if len(data) > 5_000_000:
        # Large tracked files are checked separately by path/policy; avoid noisy scans.
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def scan_text(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    emails = [x for x in EMAIL_RE.findall(text) if x.lower() not in ALLOWED_EXACT]
    phone_hits = []
    for match in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 10 <= len(digits) <= 13:
            phone_hits.append(match.group(0))
    return {
        "emails": len(emails),
        "phones": len(phone_hits),
        "secret_hints": len(SECRET_HINT_RE.findall(text)),
    }


def main() -> int:
    files = git_ls_files()
    failures: list[str] = []
    summary = {
        "tracked_files": len(files),
        "forbidden_paths": [],
        "contact_or_secret_hits": [],
    }

    for rel in files:
        if rel.startswith(FORBIDDEN_PREFIXES) or rel.endswith(FORBIDDEN_SUFFIXES):
            failures.append(f"Forbidden tracked runtime/generated path: {rel}")
            summary["forbidden_paths"].append(rel)
            continue
        path = ROOT / rel
        if not path.exists() or not is_text(path):
            continue
        hits = scan_text(path)
        if any(hits.values()):
            failures.append(f"Sensitive pattern in tracked file: {rel} {hits}")
            summary["contact_or_secret_hits"].append({"path": rel, **hits})

    print("GITHUB_PUBLIC_SAFETY_AUDIT")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        print("FAILURES:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("GITHUB_PUBLIC_SAFETY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
