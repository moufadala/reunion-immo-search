#!/usr/bin/env python3
"""Pure tests for identity-gated duplicate keys.

Pin Moufadal's rule: price+surface resemblance alone is not enough; a duplicate
key must include a real identity signal: landlord, significant title words, or
exact non-round rent.
"""
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


def row(source: str, sid: str, title: str, rent: int, surface: float, rooms: int = 3, agency: str | None = None) -> dict:
    return {
        "source_site": source,
        "source_id": sid,
        "title": title,
        "rent_eur": rent,
        "surface_m2": surface,
        "rooms": rooms,
        "agency_or_owner": agency,
    }


def enriched(city: str = "Saint-Denis", ptype: str = "Appartement") -> dict:
    return {"city_normalized": city, "property_type_normalized": ptype}


def test_price_surface_only_stays_singleton() -> None:
    a = row("a", "1", "T2 Saint-Denis", 900, 47)
    b = row("b", "2", "Appartement centre", 900, 47.5)
    assert ndb.duplicate_identity_token(a) is None
    assert ndb.duplicate_key(enriched(), a).startswith("single|a|1")
    assert ndb.duplicate_key(enriched(), a) != ndb.duplicate_key(enriched(), b)


def test_same_landlord_can_group_when_numbers_match() -> None:
    a = row("a", "1", "T3 Moufia", 1100, 65, agency="ARH Immobilier")
    b = row("b", "2", "Appartement T3", 1100, 66, agency="ARH IMMOBILIER")
    assert ndb.duplicate_identity_token(a) == ndb.duplicate_identity_token(b)
    assert ndb.duplicate_key(enriched(), a) == ndb.duplicate_key(enriched(), b)


def test_significant_title_words_are_identity_signal() -> None:
    a = row("a", "1", "T4 meublé Saint Denis centre", 1400, 81)
    b = row("b", "2", "A louer T4 meublé Saint Denis centre", 1400, 81.2)
    assert ndb.duplicate_identity_token(a) == ndb.duplicate_identity_token(b)
    assert ndb.duplicate_key(enriched(), a) == ndb.duplicate_key(enriched(), b)


def test_exact_non_round_rent_is_identity_signal() -> None:
    a = row("a", "1", "T3", 1394, 63.8)
    b = row("b", "2", "Appartement", 1394, 64.0)
    assert ndb.duplicate_identity_token(a) == "nonround-rent:1394"
    assert ndb.duplicate_key(enriched(), a) == ndb.duplicate_key(enriched(), b)


def test_round_rent_without_identity_does_not_group() -> None:
    a = row("a", "1", "T3", 900, 63.8)
    b = row("b", "2", "Appartement", 900, 64.0)
    assert ndb.duplicate_identity_token(a) is None
    assert ndb.duplicate_key(enriched(), a) != ndb.duplicate_key(enriched(), b)


def test_generic_landlord_role_is_not_identity_signal() -> None:
    a = row("a", "1", "T3", 900, 63.8, agency="private")
    b = row("b", "2", "Appartement", 900, 64.0, agency="Particulier")
    assert ndb.duplicate_identity_token(a) is None
    assert ndb.duplicate_identity_token(b) is None
    assert ndb.duplicate_key(enriched(), a) != ndb.duplicate_key(enriched(), b)


def main() -> int:
    for test in [
        test_price_surface_only_stays_singleton,
        test_same_landlord_can_group_when_numbers_match,
        test_significant_title_words_are_identity_signal,
        test_exact_non_round_rent_is_identity_signal,
        test_round_rent_without_identity_does_not_group,
        test_generic_landlord_role_is_not_identity_signal,
    ]:
        test()
    print("IDENTITY_DEDUP PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
