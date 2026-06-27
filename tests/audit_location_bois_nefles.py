#!/usr/bin/env python3
"""Regression audit for Bois de Nèfles / Sainte-Clotilde localisation.

The public card must show the finer district when the source text says
"BOIS DE NEFLES SAINTE CLOTILDE", without misclassifying Bois de Nèfles
Saint-Paul listings as Saint-Denis.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from immo_intelligence_layers import infer_location  # noqa: E402


def assert_eq(actual, expected, msg):
    if actual != expected:
        raise AssertionError(f"{msg}: expected={expected!r} actual={actual!r}")


def main() -> None:
    citya = {
        "title": "EXCLUSIVITE À LOUER - LOGEMENT NEUF - Appartement T3 situé à BOIS DE",
        "city": "Saint-Denis",
        "description": "Appartement T3 situé à BOIS DE NEFLES SAINTE CLOTILDE, dans le code postal 97490.",
    }
    loc = infer_location(citya)
    assert_eq(loc["district_best"], "Bois de Nèfles Sainte-Clotilde", "fine district")
    assert_eq(loc["precise_location_label"], "Bois de Nèfles · Saint-Denis", "public label")
    assert_eq(loc["commune_inferred"], "Saint-Denis", "commune")

    reverse = {
        "title": "A LOUER STUDIO - STE CLOTILDE BOIS DE NEFLES",
        "city": "Saint-Denis",
        "description": "situé à Sainte Clotilde quartier Bois de Nèfles",
    }
    loc2 = infer_location(reverse)
    assert_eq(loc2["precise_location_label"], "Bois de Nèfles · Saint-Denis", "reverse alias label")

    saint_paul = {
        "title": "location appartement bois de nefles st paul 97411",
        "city": "Saint-Paul",
        "description": "Appartement à Bois de Nèfles St Paul 97411",
    }
    loc3 = infer_location(saint_paul)
    if loc3["commune_inferred"] == "Saint-Denis":
        raise AssertionError(f"Saint-Paul Bois de Nèfles misclassified as Saint-Denis: {loc3}")

    print({"ok": True, "citya": loc, "reverse": loc2, "saint_paul": loc3})


if __name__ == "__main__":
    main()
