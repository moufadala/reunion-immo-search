import pytest

from src.geo_scope_guard import manifest_outside_scope
from src.publication_policy import evaluate_publication


OUTSIDE_COMMUNES = [
    "Le Tampon",
    "Saint-Pierre",
    "Saint-Paul",
    "La Possession",
    "Saint-Leu",
    "Saint-André",
    "Saint-Louis",
    "Le Port",
    "Sainte-Suzanne",
    "Saint-Benoît",
    "Saint-Joseph",
    "Bras-Panon",
    "Entre-Deux",
    "Les Avirons",
    "Étang-Salé",
    "La Plaine des Palmistes",
    "Saint-Gilles les Bains",
    "La Saline",
    "Petite Île",
    "Cilaos",
    "Salazie",
    "Trois-Bassins",
    "Saint-Philippe",
]


@pytest.mark.parametrize("locality", OUTSIDE_COMMUNES)
@pytest.mark.parametrize("field", ["title", "url", "location_label", "address"])
def test_explicit_non_target_reunion_commune_is_detected_in_strong_fields(
    locality: str, field: str
) -> None:
    value = locality
    if field == "title":
        value = f"Appartement situé à {locality}"
    elif field == "url":
        value = "https://portal.test/location/" + locality.replace(" ", "-")
    elif field == "address":
        value = f"12 rue des Fleurs, {locality}"

    evidence = manifest_outside_scope({"commune": "Saint-Denis", field: value})

    assert evidence is not None, (locality, field)


@pytest.mark.parametrize("locality", OUTSIDE_COMMUNES)
def test_each_non_target_commune_remains_allowed_when_only_a_proximity(locality: str) -> None:
    row = {
        "surface": 80,
        "rent": 1200,
        "commune": "Sainte-Marie",
        "title": "Maison familiale",
        "description": f"Maison à Sainte-Marie, proche de {locality}.",
    }

    assert evaluate_publication(row).eligible, locality


@pytest.mark.parametrize("target", ["Saint-Denis", "Sainte-Marie", "Sainte-Clotilde"])
@pytest.mark.parametrize("field", ["title", "url", "location_label", "address", "description"])
def test_target_communes_and_saint_denis_alias_are_never_outside_scope(target: str, field: str) -> None:
    assert manifest_outside_scope({field: target}) is None
