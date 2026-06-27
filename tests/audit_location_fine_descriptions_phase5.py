#!/usr/bin/env python3
"""Phase 5 regression audit: fine locations found in source descriptions."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from immo_intelligence_layers import infer_location  # noqa: E402


def assert_location(title: str, description: str, commune: str, expected_district: str, expected_commune: str) -> dict:
    got = infer_location({"title": title, "description": description, "commune": commune})
    if got.get("district_best") != expected_district or got.get("commune_inferred") != expected_commune:
        raise AssertionError(
            {
                "title": title,
                "description": description,
                "expected_district": expected_district,
                "expected_commune": expected_commune,
                "got": got,
            }
        )
    return got


def assert_no_district(title: str, description: str, commune: str, expected_commune: str) -> dict:
    got = infer_location({"title": title, "description": description, "commune": commune})
    if got.get("district_best") or got.get("commune_inferred") != expected_commune:
        raise AssertionError(
            {
                "title": title,
                "description": description,
                "expected_commune": expected_commune,
                "got": got,
            }
        )
    return got


def main() -> int:
    positives = [
        ("T2 Saint-Paul", "Appartement proche la saline les bains", "Saint-Paul", "La Saline les Bains", "Saint-Paul"),
        ("T2 Saint-Paul", "Appartement à saline les bains", "Saint-Paul", "La Saline les Bains", "Saint-Paul"),
        ("T2 Saint-Paul", "Secteur la-saline-les-bains 97434", "Saint-Paul", "La Saline les Bains", "Saint-Paul"),
        ("Studio Saint-Paul", "A deux pas de trou d'eau", "Saint-Paul", "Trou d'Eau", "Saint-Paul"),
        ("Studio Saint-Paul", "Proche trou deau", "Saint-Paul", "Trou d'Eau", "Saint-Paul"),
        ("Studio Saint-Paul", "Annonce secteur trou-d-eau", "Saint-Paul", "Trou d'Eau", "Saint-Paul"),
        ("Local Saint-Paul", "Terrain secteur de cambaie", "Saint-Paul", "Cambaie", "Saint-Paul"),
        ("Maison Saint-Paul", "Bois de Nèfles Saint Paul, calme", "Saint-Paul", "Bois de Nèfles Saint-Paul", "Saint-Paul"),
        ("Maison Saint-Paul", "BDN st paul avec jardin", "Saint-Paul", "Bois de Nèfles Saint-Paul", "Saint-Paul"),
        ("Maison Saint-Pierre", "Quartier de la Ravine des Cabris", "Saint-Pierre", "Ravine des Cabris", "Saint-Pierre"),
        ("Maison Saint-Pierre", "Concession Ravine des Cabris", "Saint-Pierre", "Ravine des Cabris", "Saint-Pierre"),
        ("Villa Saint-Pierre", "Saint-Pierre Ligne des Bambous", "Saint-Pierre", "Ligne des Bambous", "Saint-Pierre"),
        ("Appartement Saint-Pierre", "Quartier de bassin plat", "Saint-Pierre", "Bassin Plat", "Saint-Pierre"),
        ("Appartement Saint-Pierre", "Secteur b.plat", "Saint-Pierre", "Bassin Plat", "Saint-Pierre"),
        ("Villa Saint-Pierre", "Condé-Concession proche commodités", "Saint-Pierre", "Condé-Concession", "Saint-Pierre"),
        ("T3 La Possession", "Moulin Joli, résidence calme", "La Possession", "Moulin Joli", "La Possession"),
        ("Appartement La Montagne Saint-Denis", "Belle vue", "Saint-Denis", "La Montagne", "Saint-Denis"),
    ]
    evidence = [
        assert_location(title, description, commune, expected_district, expected_commune)
        for title, description, commune, expected_district, expected_commune in positives
    ]

    negatives = [
        ("Appartement La Possession", "Vue mer et montagne", "La Possession", "La Possession"),
        ("Appartement La Possession", "Belle vue sur la montagne", "La Possession", "La Possession"),
        ("Maison La Possession", "Route de la Montagne à proximité", "La Possession", "La Possession"),
    ]
    negative_evidence = [assert_no_district(*case) for case in negatives]
    print({"ok": True, "positive_checked": len(evidence), "negative_checked": len(negative_evidence), "sample": evidence[0]})
    print("LOCATION_FINE_DESCRIPTIONS_PHASE5_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
