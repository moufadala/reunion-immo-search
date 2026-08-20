from __future__ import annotations

import pytest

from src.geo_scope_guard import manifest_outside_scope
from src.publication_policy import evaluate_publication


@pytest.mark.parametrize("outside_city", ["Saint-Paul", "Saint-André"])
def test_target_commune_with_structured_outside_city_is_rejected(outside_city: str) -> None:
    row = {
        "surface": 80,
        "rent": 1200,
        "commune": "Saint-Denis",
        "city": outside_city,
        "address": "12 rue des Tamarins, Saint-Denis",
        "title": "Appartement familial",
        "description": "À proximité des commerces.",
    }

    evidence = manifest_outside_scope(row)

    assert evidence is not None
    assert evidence.field == "city"
    assert evaluate_publication(row).reason == "manifest_outside_scope"


def test_outside_commune_with_structured_target_city_is_rejected() -> None:
    row = {
        "surface": 80,
        "rent": 1200,
        "commune": "Saint-Paul",
        "city": "Saint-Denis",
        "title": "Appartement familial",
        "description": "Avec parking.",
    }

    assert not evaluate_publication(row).eligible
    assert evaluate_publication(row).reason == "commune_outside_scope"
