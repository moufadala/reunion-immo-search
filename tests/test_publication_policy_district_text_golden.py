import pytest

from src.publication_policy import evaluate_publication


def listing(**overrides):
    base = {"surface": 70, "rent": 1300, "commune": "Saint-Denis", "district": "Moufia"}
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "title,description",
    [
        ("location appartement st denis", "Un véritable coup de coeur dans les bas de Saint-François !"),
        ("location appartement st denis", "Localisation : Saint-Denis, bas de Saint-François."),
        ("A louer Grand T4 Saint-Francois", "Appartement agréable."),
        ("Location Appartement Saint Denis", "En EXCLUSIVITE, secteur bas de Saint-François."),
        ("Appartement 4 pièces", "À louer Appartement T4 - Bas de Saint-François"),
    ],
)
def test_real_candidate_saint_francois_localisations_are_excluded(title, description):
    decision = evaluate_publication(listing(title=title, description=description))

    assert not decision.eligible
    assert decision.reason == "saint_denis_saint_francois"


@pytest.mark.parametrize(
    "description",
    [
        "Adresse administrative : rue Saint-François, Saint-Denis.",
        "Appartement proche de Saint-François avec accès rapide au boulevard.",
        "À 5 minutes du quartier Saint-François.",
        "À côté de l'école Saint-François.",
        "Contact : François Saint, agent immobilier.",
    ],
)
def test_saint_francois_non_location_mentions_are_not_excluded(description):
    assert evaluate_publication(listing(description=description)).eligible


@pytest.mark.parametrize(
    "description",
    [
        "Appartement situé dans le quartier de la Providence à Saint-Denis.",
        "Localisation : secteur Providence, Saint-Denis.",
    ],
)
def test_providence_explicit_localisations_are_excluded(description):
    decision = evaluate_publication(listing(description=description))

    assert not decision.eligible
    assert decision.reason == "saint_denis_providence"


@pytest.mark.parametrize(
    "description",
    [
        "Accès par la rue de la Providence.",
        "Proche de la Providence.",
        "À 3 km de la Providence.",
    ],
)
def test_providence_non_location_mentions_are_not_excluded(description):
    assert evaluate_publication(listing(description=description)).eligible
