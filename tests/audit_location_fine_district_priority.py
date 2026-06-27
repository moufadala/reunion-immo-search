#!/usr/bin/env python3
"""Regression audit: fine Saint-Denis districts must not be downgraded to Sainte-Clotilde."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from immo_intelligence_layers import infer_location  # noqa: E402


def check(title: str, expected: str) -> dict:
    got = infer_location({"title": title, "description": "", "commune": "Saint-Denis"})
    label = got.get("precise_location_label") or ""
    district = got.get("district_best") or ""
    if expected not in {label, district} and not label.startswith(expected + " ·"):
        raise AssertionError({"title": title, "expected": expected, "got": got})
    return got


def main() -> int:
    cases = {
        "Appartement Sainte-Clotilde Moufia proche université": "Moufia",
        "T2 Sainte Clotilde Les Camélias": "Les Camélias",
        "Maison Sainte-Clotilde Domenjod": "Domenjod",
        "Studio Sainte-Clotilde Champ Fleuri": "Champ Fleuri",
        "Appartement Sainte-Clotilde Providence": "Providence",
        "T3 Sainte-Clotilde Bellepierre": "Bellepierre",
        "T4 Sainte-Clotilde Montgaillard": "Montgaillard",
        "T2 Bois de Nèfles Sainte-Clotilde": "Bois de Nèfles · Saint-Denis",
    }
    evidence = {title: check(title, expected) for title, expected in cases.items()}
    # Non-regression: Bois de Nèfles Saint-Paul must not be forced into Saint-Denis.
    west = infer_location({"title": "Appartement Bois de Nèfles Saint-Paul", "description": "", "commune": "Saint-Paul"})
    if west.get("commune_inferred") != "Saint-Paul":
        raise AssertionError({"title": "Bois de Nèfles Saint-Paul", "got": west})
    print({"ok": True, "checked": len(cases) + 1, "sample": evidence["Appartement Sainte-Clotilde Moufia proche université"], "west": west})
    print("LOCATION_FINE_DISTRICT_PRIORITY_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
