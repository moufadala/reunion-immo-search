import pytest

from src.geo_scope_guard import manifest_outside_scope
from src.publication_policy import evaluate_publication


def _listing(source: str, source_id: str, **overrides: object) -> dict:
    listing = {
        "id": f"{source}:{source_id}",
        "source": source,
        "surface": 80,
        "rent": 1200,
        # The regression is precisely that the DB commune can be wrong.
        "commune": "Saint-Denis",
        "title": "Appartement T3",
        "description": "Appartement familial avec varangue et parking.",
        "url": f"https://example.test/{source}/{source_id}",
    }
    listing.update(overrides)
    return listing


REAL_OUTSIDE_SCOPE_GOLDENS = [
    _listing(
        "citya",
        "GES27390302-542",
        title="Location appartement 3 pièces Saint-André",
        url="https://www.citya.com/annonces/location/appartement/saint-andre-97440/GES27390302-542",
    ),
    _listing(
        "citya",
        "GES56851227-542",
        title="Maison 4 pièces",
        description="Maison située à Saint André, proche des commerces.",
    ),
    _listing("ofim_rss", "73342", title="Appartement aux Avirons"),
    _listing("ofim_rss", "72988", title="Maison à Sainte-Suzanne"),
    _listing("ofim_rss", "73117", description="Location située à Sainte Suzanne."),
    _listing("ofim_rss", "73376", title="Villa Sainte-Suzanne"),
    _listing("ofim_rss", "73375", title="Maison à Saint-Philippe"),
    _listing("ofim_rss", "73365", title="Maison Plaine des Palmistes"),
    _listing("ofim_rss", "73368", title="Villa au Tévelave"),
]


@pytest.mark.parametrize("candidate", REAL_OUTSIDE_SCOPE_GOLDENS, ids=lambda row: row["id"])
def test_real_mislabeled_outside_scope_rows_are_rejected(candidate: dict) -> None:
    evidence = manifest_outside_scope(candidate)

    assert evidence is not None, candidate["id"]
    assert evaluate_publication(candidate).reason == "manifest_outside_scope"


def test_citya_outside_scope_url_is_sufficient_when_visible_text_is_generic() -> None:
    candidate = _listing(
        "citya",
        "GES27390302-542",
        url="https://www.citya.com/annonces/location/appartement/saint-andre-97440/GES27390302-542",
    )

    evidence = manifest_outside_scope(candidate)

    assert evidence is not None
    assert evidence.field == "url"
    assert evidence.locality == "Saint-André"


@pytest.mark.parametrize(
    "description",
    [
        "Appartement à Saint-Denis, proche de Saint-André.",
        "Maison à Sainte-Marie, à proximité de Sainte-Suzanne.",
        "Logement à Saint-Denis, à 8 km des Avirons.",
        "Appartement à Saint-Denis, proche du Tévelave.",
    ],
)
def test_outside_locality_used_only_as_proximity_does_not_exclude(description: str) -> None:
    assert evaluate_publication(_listing("portal", "1", description=description)).eligible


def test_real_zimo_commercial_lease_is_not_public_residential_inventory() -> None:
    candidate = _listing(
        "zimo",
        "019feba5",
        title="Bail commercial à céder",
        description="Cession d'un bail commercial pour une activité de restauration.",
    )

    assert evaluate_publication(candidate).reason == "non_residential_commercial"


@pytest.mark.parametrize(
    "description",
    [
        "Appartement avec un espace bureau dans une chambre.",
        "Maison proche du centre commercial et des écoles.",
    ],
)
def test_residential_mentions_of_office_or_shopping_stay_eligible(description: str) -> None:
    assert evaluate_publication(_listing("portal", "home", description=description)).eligible
