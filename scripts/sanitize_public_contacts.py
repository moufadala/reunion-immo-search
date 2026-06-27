#!/usr/bin/env python3
"""Redact contact details from public static Immo artifacts before publishing/versioning.

The portal can keep listing descriptions for user value, but direct emails and
phone numbers from source ads should not be committed to GitHub/public JSON/HTML.
This script is intentionally deterministic and local: it rewrites only text files
passed on the command line and prints per-file replacement counts.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
# Réunion/FR-like phone numbers, allowing separators/spaces. Avoid matching IDs by
# requiring a telecom-ish prefix and at least 10 digits after normalization.
PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\+?262|0)(?:[\s.\-/]*(?:262|692|693|6|7|2))?(?:[\s.\-/]*\d){7,9}(?![A-Za-z0-9])"
)


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    counts = {"email": 0, "phone": 0}

    def email_sub(_: re.Match[str]) -> str:
        counts["email"] += 1
        return "[contact email masqué]"

    def phone_sub(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        # Keep non-phone numeric identifiers out of the sanitizer.
        if len(digits) < 10 or len(digits) > 13:
            return m.group(0)
        counts["phone"] += 1
        return "[contact téléphone masqué]"

    text = EMAIL_RE.sub(email_sub, text)
    text = PHONE_RE.sub(phone_sub, text)
    return text, counts


def main() -> int:
    ap = argparse.ArgumentParser(description="Redact emails/phones in static public artifacts")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--check", action="store_true", help="Fail if replacements would be needed; do not write")
    args = ap.parse_args()

    report: dict[str, dict[str, int]] = {}
    changed = False
    for path in args.files:
        original = path.read_text(encoding="utf-8", errors="replace")
        redacted, counts = redact_text(original)
        total = counts["email"] + counts["phone"]
        report[str(path)] = counts | {"total": total}
        if redacted != original:
            changed = True
            if not args.check:
                path.write_text(redacted, encoding="utf-8")
    print(json.dumps({"ok": not (args.check and changed), "changed": changed, "files": report}, ensure_ascii=False, indent=2))
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
