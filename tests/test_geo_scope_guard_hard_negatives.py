import pytest

from src.publication_policy import evaluate_publication


def _listing(description: str) -> dict:
    return {
        "surface": 80,
        "rent": 1200,
        "commune": "Sainte-Marie",
        "title": "Maison familiale",
        "description": description,
    }


@pytest.mark.parametrize(
    "description",
    [
        "Logement situé à Sainte-Marie. Contactez notre agence de Saint-André.",
        "Maison à Sainte-Marie avec vue dégagée vers Sainte-Suzanne.",
        "Appartement à Sainte-Marie avec vue sur Saint-André.",
    ],
)
def test_agency_or_view_context_is_not_misread_as_property_location(description: str) -> None:
    assert evaluate_publication(_listing(description)).eligible
