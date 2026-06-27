#!/usr/bin/env python3
"""Acceptance audit for private immo watch profiles.

This test is intentionally fixture-based and non-networked. It verifies that
generic private criteria are represented as dry-run-only profiles and that the
matching layer explains strict/fallback/reject decisions without live messaging.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "private_profiles.example.json"
SCRIPT = ROOT / "src" / "private_profile_matching.py"

errors: list[str] = []
if not CONFIG.exists():
    errors.append("missing config/private_profiles.example.json")
if not SCRIPT.exists():
    errors.append("missing src/private_profile_matching.py")
if errors:
    print("PRIVATE_PROFILES_AUDIT FAIL", errors)
    raise SystemExit(1)

cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
profiles = {p.get("id"): p for p in cfg.get("profiles", [])}
required = {"sample_north_rental_small", "sample_north_rental_large"}
missing = required - profiles.keys()
if missing:
    errors.append(f"missing profiles: {sorted(missing)}")

if cfg.get("mode") != "dry_run_only":
    errors.append("config.mode must be dry_run_only")
if cfg.get("telegram_live_enabled") is not False:
    errors.append("telegram_live_enabled must be false in example config")
if cfg.get("scope") != "private":
    errors.append("config.scope must be private")
if "example private watch profiles" not in json.dumps(cfg, ensure_ascii=False).lower():
    errors.append("example must be explicit that it is generic private scope")

small = profiles.get("sample_north_rental_small", {})
large = profiles.get("sample_north_rental_large", {})
for pid, profile in profiles.items():
    if profile.get("scope") != "private":
        errors.append(f"{pid}: scope must be private")
    if profile.get("alert_mode") != "dry_run":
        errors.append(f"{pid}: alert_mode must be dry_run")
    wanted_events = set(profile.get("events", []))
    for ev in ["new", "price_drop", "reappeared", "useful_enrichment", "significant_change"]:
        if ev not in wanted_events:
            errors.append(f"{pid}: missing event {ev}")

if small:
    zones = set(small.get("criteria", {}).get("zones", []))
    for z in ["Duparc", "Les Cafés", "Rivière des Pluies", "La Bretagne"]:
        if z not in zones:
            errors.append(f"small missing zone {z}")
    crit = small.get("criteria", {})
    if crit.get("surface_min_m2") != 60 or crit.get("rent_min_eur") != 850 or crit.get("rent_max_eur") != 1000 or crit.get("bedrooms_min") != 1:
        errors.append("small numeric criteria mismatch")
    if "furnished" not in small.get("fallbacks", {}) or "floor_without_elevator" not in small.get("warnings", []):
        errors.append("small fallback/warning policy incomplete")

if large:
    zones = set(large.get("criteria", {}).get("zones", []))
    for z in ["Duparc", "Les Cafés", "Rivière des Pluies", "La Bretagne", "Moufia"]:
        if z not in zones:
            errors.append(f"large missing zone {z}")
    crit = large.get("criteria", {})
    if crit.get("surface_min_m2") != 115 or crit.get("rent_min_eur") != 900 or crit.get("rent_max_eur") != 1400 or crit.get("bedrooms_min") != 3:
        errors.append("large numeric criteria mismatch")
    if large.get("fallbacks", {}).get("bedrooms_min") != 2 or "garden_preferred" not in large.get("preferences", []):
        errors.append("large fallback/preference policy incomplete")

fixtures = {
    "listings": [
        {
            "id": "strict-small",
            "title": "Appartement non meublé Duparc RDC",
            "region": "Nord",
            "commune": "Sainte-Marie",
            "primary_zone": "Duparc",
            "zones": ["Duparc", "Sainte-Marie"],
            "property_type": "Appartement",
            "furnished": "Non meublé",
            "rent_eur": 930,
            "surface_m2": 65,
            "bedrooms": 1,
            "description": "RDC proche Duparc, non meublé.",
            "url": "https://example.test/strict-small",
        },
        {
            "id": "fallback-small-furnished",
            "title": "Maison meublée Les Cafés 70m2",
            "region": "Nord",
            "commune": "Sainte-Marie",
            "primary_zone": "Les Cafés",
            "zones": ["Les Cafés", "Sainte-Marie"],
            "property_type": "Maison",
            "furnished": "Meublé",
            "rent_eur": 990,
            "surface_m2": 70,
            "bedrooms": 2,
            "description": "Maison meublée secteur Les Cafés.",
            "url": "https://example.test/fallback-small",
        },
        {
            "id": "fallback-large-2bed",
            "title": "Maison Moufia jardin 2 chambres",
            "region": "Nord",
            "commune": "Saint-Denis",
            "primary_zone": "Moufia",
            "zones": ["Moufia", "Saint-Denis"],
            "property_type": "Maison",
            "furnished": "Non meublé",
            "rent_eur": 1320,
            "surface_m2": 125,
            "bedrooms": 2,
            "description": "Maison non meublée avec jardin au Moufia, deux chambres.",
            "url": "https://example.test/fallback-large",
        },
        {
            "id": "reject-too-expensive",
            "title": "Maison La Bretagne trop chère",
            "region": "Nord",
            "commune": "Saint-Denis",
            "primary_zone": "La Bretagne",
            "zones": ["La Bretagne"],
            "property_type": "Maison",
            "furnished": "Non meublé",
            "rent_eur": 1800,
            "surface_m2": 130,
            "bedrooms": 4,
            "description": "Maison avec jardin.",
            "url": "https://example.test/reject",
        },
    ]
}

import tempfile
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    listings = tmp / "listings.json"
    out = tmp / "dry_run.json"
    listings.write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(CONFIG), "--listings", str(listings), "--dry-run", "--json-out", str(out)],
        cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    if proc.returncode != 0:
        errors.append(f"dry-run failed rc={proc.returncode} stderr={proc.stderr[:400]}")
    else:
        data = json.loads(out.read_text(encoding="utf-8"))
        if not data.get("ok") or not data.get("dry_run") or data.get("telegram_live_enabled") is not False:
            errors.append("dry-run output missing ok/dry_run/live=false")
        by_profile = {p["profile_id"]: p for p in data.get("profiles", [])}
        small_profile = by_profile.get("sample_north_rental_small", {})
        large_profile = by_profile.get("sample_north_rental_large", {})
        def decision(profile: dict, listing_id: str) -> str | None:
            for item in profile.get("matches", []):
                if item.get("id") == listing_id:
                    return item.get("decision")
            return None
        if decision(small_profile, "strict-small") != "strict_match":
            errors.append("small profile strict fixture should be strict_match")
        if decision(small_profile, "fallback-small-furnished") != "fallback_match":
            errors.append("small profile furnished fixture should be fallback_match")
        if decision(large_profile, "fallback-large-2bed") != "fallback_match":
            errors.append("large profile 2-bedroom fixture should be fallback_match")
        if decision(large_profile, "reject-too-expensive") is not None:
            errors.append("too expensive fixture must not appear as large profile match")
        for profile in data.get("profiles", []):
            for item in profile.get("matches", []):
                if not item.get("reasons"):
                    errors.append(f"{profile.get('profile_id')}:{item.get('id')} missing reasons")
                if item.get("decision") == "fallback_match" and not item.get("warnings"):
                    errors.append(f"{profile.get('profile_id')}:{item.get('id')} fallback without warnings")
        if "TELEGRAM" in proc.stdout.upper() or "LIVE" in proc.stdout.upper():
            errors.append("stdout must not look like live Telegram emission")

if errors:
    print("PRIVATE_PROFILES_AUDIT FAIL")
    print(json.dumps(errors, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print("PRIVATE_PROFILES_AUDIT PASS")
