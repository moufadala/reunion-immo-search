from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

profils = types.ModuleType("profils")
profils.PROFILS = {}
profils.scorer = lambda listing, key: {"score": 0}
sys.modules["profils"] = profils
from export_feed import (
    active_public_listings,
    detail_text_ok,
    excluded_quartier_from_description,
    excluded_quartier_from_field,
    norm,
    public_rule_violation,
)


def test_feed_listings_contains_only_active_rows() -> None:
    rows = [{"id": "active", "active": True}, {"id": "gone", "active": False}]
    assert active_public_listings(rows) == [{"id": "active", "active": True}]


def test_public_rule_surface_and_rent_are_strict_and_unknowns_excluded() -> None:
    assert public_rule_violation({"rent": None, "surface": 80}) == "loyer_inconnu"
    assert public_rule_violation({"rent": 1701, "surface": 80}) == "loyer_sup_1700"
    assert public_rule_violation({"rent": 1700, "surface": None}) == "surface_inconnue"
    assert public_rule_violation({"rent": 1700, "surface": 64.9}) == "surface_inf_65"
    assert public_rule_violation({"rent": 1700, "surface": 65}) is None


def test_saint_denis_excluded_quartier_variants_are_normalized() -> None:
    assert excluded_quartier_from_field("La Providence") == "Providence"
    assert excluded_quartier_from_field("Providence") == "Providence"
    assert excluded_quartier_from_field("St-François") == "Saint-François"
    assert excluded_quartier_from_field("Saint Francois") == "Saint-François"
    assert norm("St-François") == "saint-francois"


def test_excluded_quartier_from_description_requires_location_context() -> None:
    assert excluded_quartier_from_description("T3", "Appartement situé à La Providence") == "Providence"
    assert excluded_quartier_from_description("T3", "Location à St François") == "Saint-François"
    assert excluded_quartier_from_description("T3", "Belle vue sur la providence") is None
    assert excluded_quartier_from_description("T3", "À 5 minutes de Saint François") is None


def test_detail_text_ok_does_not_count_empty_http_200_as_read() -> None:
    assert detail_text_ok("") is False
    assert detail_text_ok("   ") is False
    assert detail_text_ok("texte trop court") is False
    assert detail_text_ok("x" * 80) is True
