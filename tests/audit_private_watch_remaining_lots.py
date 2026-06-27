#!/usr/bin/env python3
"""Acceptance audit for the remaining private-watch lots.

Covers:
- Les Cafés as a fine Sainte-Marie district.
- near_miss output when strict/fallback is empty.
- human Telegram-style dry-run digest without live transport.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from immo_intelligence_layers import infer_location  # noqa: E402

SCRIPT = ROOT / "src" / "private_profile_matching.py"
CONFIG = ROOT / "config" / "private_profiles.example.json"
errors: list[str] = []

# 1) Les Cafés must be inferred as a fine district, not just broad Sainte-Marie.
loc = infer_location({"title": "Appartement Les Cafés Sainte-Marie", "description": "", "commune": "Sainte-Marie"})
if loc.get("district_best") != "Les Cafés" or loc.get("commune_inferred") != "Sainte-Marie":
    errors.append(f"Les Cafés district inference failed: {loc}")

fixtures = {
    "listings": [
        {
            "id": "near-miss-mother-price-high",
            "title": "Appartement Les Cafés non meublé 60m2",
            "region": "Nord",
            "commune": "Sainte-Marie",
            "primary_zone": "Les Cafés",
            "zones": ["Les Cafés", "Sainte-Marie"],
            "property_type": "Appartement",
            "furnished": "Non meublé",
            "rent_eur": 1060,
            "surface_m2": 62,
            "bedrooms": 1,
            "description": "Appartement non meublé Les Cafés au premier étage.",
            "url": "https://example.test/near-miss-mother",
        },
        {
            "id": "near-miss-family-surface-low",
            "title": "Maison Moufia jardin non meublée 108m2 3 chambres",
            "region": "Nord",
            "commune": "Saint-Denis",
            "primary_zone": "Moufia",
            "zones": ["Moufia", "Saint-Denis"],
            "property_type": "Maison",
            "furnished": "Non meublé",
            "rent_eur": 1350,
            "surface_m2": 108,
            "bedrooms": 3,
            "description": "Maison non meublée avec jardin au Moufia.",
            "url": "https://example.test/near-miss-family",
        },
    ]
}

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    listings = tmp / "listings.json"
    out = tmp / "out.json"
    listings.write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(CONFIG), "--listings", str(listings), "--dry-run", "--include-near-miss", "--digest", "--json-out", str(out)],
        cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    if proc.returncode != 0:
        errors.append(f"dry-run near-miss/digest failed rc={proc.returncode} stderr={proc.stderr[:400]}")
    else:
        data = json.loads(out.read_text(encoding="utf-8"))
        if not data.get("dry_run") or data.get("telegram_live_enabled") is not False:
            errors.append("payload should remain dry-run with telegram_live_enabled=false")
        digest = data.get("digest_text") or ""
        if "DRY-RUN" not in digest or "AUCUN ENVOI" not in digest or "near_miss" not in digest:
            errors.append(f"digest missing dry-run/no-send/near_miss markers: {digest[:300]}")
        if "https://example.test/near-miss" not in digest:
            errors.append("digest should include source URLs for review")
        by_profile = {p["profile_id"]: p for p in data.get("profiles", [])}
        mother = by_profile.get("mother_sainte_marie_rental", {})
        family = by_profile.get("moufadal_family_rental", {})
        def near_ids(profile: dict) -> set[str]:
            return {x.get("id") for x in profile.get("near_misses", [])}
        if "near-miss-mother-price-high" not in near_ids(mother):
            errors.append("mother near miss price-high fixture not reported")
        if "near-miss-family-surface-low" not in near_ids(family):
            errors.append("family near miss surface-low fixture not reported")
        for profile in data.get("profiles", []):
            for item in profile.get("near_misses", []):
                if item.get("decision") != "near_miss" or not item.get("miss_reasons"):
                    errors.append(f"bad near_miss item: {item}")

if errors:
    print("PRIVATE_WATCH_REMAINING_LOTS_AUDIT FAIL")
    print(json.dumps(errors, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print("PRIVATE_WATCH_REMAINING_LOTS_AUDIT PASS")
