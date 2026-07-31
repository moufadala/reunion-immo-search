#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "src" / "normalize_db.py"
spec = importlib.util.spec_from_file_location("normalize_db", MOD_PATH)
assert spec is not None and spec.loader is not None
ndb = importlib.util.module_from_spec(spec)
sys.modules["normalize_db"] = ndb
spec.loader.exec_module(ndb)


def test_cpi_number_in_description_does_not_override_city() -> None:
    row = {
        "city": "Saint-Denis",
        "district": None,
        "title": "Studio 1 pièce 19 m²",
        "description": "Carte professionnelle no CPI 97412022000000006 délivrée par CCI Réunion",
        "url": "https://example.test/location/3229536631",
        "canonical_url": None,
    }
    assert ndb.detect_commune(row) == "Saint-Denis"


def test_citya_title_location_overrides_agency_city_and_description_boilerplate() -> None:
    row = {
        "city": "Sainte-Marie",
        "district": None,
        "title": "Appartement à louer 3 pièces 65.54 m² - Le Tampon (974) - 775€",
        "description": "Barème agences Saint-Gilles-les-Bains 97434/500#bareme et Saint-Pierre 97410/214#bareme",
        "url": "https://www.citya.com/annonces/location/appartement/le-tampon-97422/GES05110025-495",
        "canonical_url": None,
    }
    assert ndb.detect_commune(row) == "Le Tampon"


def test_quartier_city_maps_to_commune() -> None:
    row = {
        "city": "Sainte-Clotilde",
        "district": "Saint-Denis - Le Moufia - Sainte-Clotilde",
        "title": "Appartement T3",
        "description": "",
        "url": "https://example.test/a",
        "canonical_url": None,
    }
    assert ndb.detect_commune(row) == "Saint-Denis"


def test_bounded_postal_does_not_match_inside_long_cpi_number() -> None:
    assert not ndb._has_postal("cpi 97412022000000006", "97412")
    assert ndb._has_postal("adresse 97412 bras panon", "97412")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("NORMALIZE_DB_COMMUNE_DETECTION PASS")
