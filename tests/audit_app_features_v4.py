#!/usr/bin/env python3
"""Static feature audit for the generated Reunion immo dashboard.

Validates non-data-loss and product features that are easy to regress:
- embedded payload keeps both listings and suspects after image cache rewrite
- localStorage powered saved/hidden UI tokens are present
- share/copy/source actions are present
- duplicate/trust fields are exported
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "artifacts" / "app"
html_path = APP / "index.html"
listings_path = APP / "listings.json"
suspects_path = APP / "suspects.json"

errors: list[str] = []
if not html_path.exists(): errors.append("missing index.html")
if not listings_path.exists(): errors.append("missing listings.json")
if not suspects_path.exists(): errors.append("missing suspects.json")
if errors:
    print("APP_FEATURE_AUDIT FAIL", errors)
    sys.exit(1)

html = html_path.read_text(encoding="utf-8")
listings = json.loads(listings_path.read_text(encoding="utf-8"))["listings"]
suspects = json.loads(suspects_path.read_text(encoding="utf-8"))["suspects"]
match = re.search(r'<script id="embeddedData" type="application/json">(.*?)</script>', html, re.S)
if not match:
    errors.append("missing embeddedData JSON")
    embedded = {"listings": [], "suspects": []}
else:
    embedded = json.loads(match.group(1))

if len(embedded.get("listings", [])) != len(listings):
    errors.append(f"embedded listings mismatch {len(embedded.get('listings', []))}!={len(listings)}")
if len(embedded.get("suspects", [])) != len(suspects):
    errors.append(f"embedded suspects mismatch {len(embedded.get('suspects', []))}!={len(suspects)}")

required_tokens = [
    "LS_SAVED", "LS_HIDDEN", "showSavedMobile", "showHiddenMobile",
    "cardActions", "modalActions", "navigator.share", "clipboard.writeText",
    "duplicate_group_size", "missing_fields", "trust_flags", "Similaires",
    "copySearch", "copySearchUrl", "stateFromUrl", "encodeStateToParams", "immo",
    "saved_search_alerts", "alertSummary",
    "zoneMap", "renderZoneMap", "source_health", "source_health.html", "saved_searches.html",
]
for token in required_tokens:
    if token not in html:
        errors.append(f"missing UI token: {token}")

dup_count = sum(1 for x in listings if (x.get("duplicate_group_size") or 1) > 1)
missing_count = sum(1 for x in listings if x.get("missing_fields"))
if dup_count <= 0:
    errors.append("no duplicate/similar groups exported")
if missing_count <= 0:
    errors.append("no missing-field trust signals exported")

print(
    "APP_FEATURE_AUDIT",
    "PASS" if not errors else "FAIL",
    f"listings={len(listings)} suspects={len(suspects)} dup={dup_count} missing_flags={missing_count}",
)
if errors:
    for e in errors:
        print(" -", e)
    sys.exit(1)
