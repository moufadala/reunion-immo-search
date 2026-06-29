#!/usr/bin/env python3
"""Audit privacy of public-served artifacts in artifacts/app/.

This guard enforces that no personal data (names, personal search criteria)
is present in the files served by nginx.  It complements audit_github_public_safety.py
which only scans git-tracked files; runtime artifacts are not tracked by git but
are served directly to the public.

P0 rules:
- saved_searches_admin.json must not exist in the public artifact
- saved_searches.html must not exist in the public artifact
- No JSON/HTML file may contain the literal personal name pattern used in saved searches

Run: python3 tests/audit_privacy_artifacts.py [path/to/app]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

APP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/app")

FORBIDDEN_FILES = {
    "saved_searches_admin.json",
    "saved_searches.html",
}

PERSONAL_NAME_RE = re.compile(r"\bMoufadal\b", re.I)

errors: list[str] = []
warnings: list[str] = []


def check() -> None:
    if not APP.exists():
        errors.append(f"app directory not found: {APP}")
        return

    for name in FORBIDDEN_FILES:
        if (APP / name).exists():
            errors.append(
                f"P0 PRIVACY: {name} must not exist in public artifact — "
                "contains personal search criteria (name, budget, family composition)"
            )

    for path in sorted(APP.iterdir()):
        if not path.is_file():
            continue
        if path.suffix not in {".json", ".html", ".txt", ".xml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if PERSONAL_NAME_RE.search(text):
            errors.append(
                f"P0 PRIVACY: personal name pattern found in {path.name} — "
                "remove personal data before publishing"
            )


check()

result = {
    "ok": len(errors) == 0,
    "app": str(APP),
    "errors": errors,
    "warnings": warnings,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
if errors:
    raise SystemExit("PRIVACY_ARTIFACTS_AUDIT FAIL")
print("PRIVACY_ARTIFACTS_AUDIT PASS")
