from src.publication_policy import evaluate_publication
import pytest



@pytest.mark.parametrize("title", [
    "Appartement proche de Saint-Francois",
    "Appartement proche du quartier Saint-Francois",
    "Appartement a proximite du quartier Saint-Francois",
])
def test_saint_francois_proximity_in_title_is_not_excluded(title):
    listing = {
        "surface": 70,
        "rent": 1300,
        "commune": "Saint-Denis",
        "district": "Moufia",
        "title": title,
    }

    assert evaluate_publication(listing).eligible
