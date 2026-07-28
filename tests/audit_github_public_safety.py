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


PERSONAL_NAME_RE = re.compile(
    r"\b(Moufadal|saved_searches_admin)\b", re.I
)
RUNTIME_APP_DIR = ROOT / "artifacts" / "app"
# JSON/HTML files in artifacts/app that are safe to expose publicly
RUNTIME_ALLOWLIST: set[str] = {
    "listings.json", "listings_index.json", "changes.json",
    "source_health.json", "dedup_groups.json", "opportunity.json",
    "locations.json", "coverage.json", "ops_status.json",
    "opportunity_calibration.json", "search_ontology.json",
    "photo_quality.json", "p0_product_polish.json", "thumbs_manifest.json",
    "build_manifest.json",
}
# Patterns that must never appear in public runtime JSON/HTML
RUNTIME_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("personal_name", PERSONAL_NAME_RE),
    ("secret_hint", SECRET_HINT_RE),
    ("email", EMAIL_RE),
]


def scan_runtime_artifacts() -> list[str]:
    """Scan public-served artifact files for personal data and secrets.

    The git guard only covers tracked files.  Runtime artifacts in artifacts/app/
    are served directly by nginx and must also be clean — they are not tracked
    by git but could leak via the public web.
    """
    issues: list[str] = []
    if not RUNTIME_APP_DIR.exists():
        return issues
    for path in sorted(RUNTIME_APP_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.suffix not in {".json", ".html", ".txt", ".xml"}:
            continue
        if path.name == "saved_searches_admin.json":
            issues.append(
                f"PRIVACY: {path.name} must not exist in public artifact "
                "(contains personal search criteria with name)"
            )
            continue
        if path.name == "saved_searches.html":
            issues.append(
                f"PRIVACY: {path.name} must not exist in public artifact "
                "(contains personal search criteria)"
            )
            continue
        if not is_text(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in RUNTIME_FORBIDDEN_PATTERNS:
            hits = pattern.findall(text)
            clean_hits = [h for h in hits if h.lower() not in ALLOWED_EXACT]
            if clean_hits:
                issues.append(
                    f"RUNTIME_PRIVACY: {path.name} contains {label} pattern: {clean_hits[:3]}"
                )
                break
    return issues


def main() -> int:
    files = git_ls_files()
    failures: list[str] = []
    summary = {
        "tracked_files": len(files),
        "forbidden_paths": [],
        "contact_or_secret_hits": [],
        "runtime_artifact_issues": [],
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

    runtime_issues = scan_runtime_artifacts()
    if runtime_issues:
        summary["runtime_artifact_issues"] = runtime_issues
        for issue in runtime_issues:
            failures.append(issue)

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
