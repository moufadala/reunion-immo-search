import pytest

from src.geo_scope_guard import manifest_outside_scope
from src.publication_policy import evaluate_publication


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("address", "12 rue Saint-Paul, Saint-Denis"),
        ("address", "8 avenue de Saint-Pierre, Sainte-Marie"),
        ("title", "Appartement Résidence Saint-Joseph"),
        ("title", "Maison près de l'École Saint-Benoît"),
        ("description", "Logement situé rue Saint-Paul à Saint-Denis."),
        ("description", "Appartement face à l'église Saint-Philippe à Sainte-Marie."),
    ],
)
def test_named_road_residence_school_or_landmark_is_not_a_commune(field: str, value: str) -> None:
    row = {
        "surface": 80,
        "rent": 1200,
        "commune": "Saint-Denis",
        "title": "Appartement familial",
        "description": "Avec varangue et parking.",
        field: value,
    }

    assert manifest_outside_scope(row) is None
    assert evaluate_publication(row).eligible
