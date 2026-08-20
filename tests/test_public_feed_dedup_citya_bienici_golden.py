import pytest

from src.public_feed_dedup import deduplicate_public_feed


def _listing(listing_id: str, source: str, reference: str, **overrides: object) -> dict:
    listing = {
        "id": listing_id,
        "source": source,
        "url": f"https://example.test/{source}/{reference}",
        "title": "Appartement familial avec varangue",
        "description": f"Location disponible. Référence agence : {reference}.",
        "commune": "Saint-Denis",
        "type": "Appartement",
        "rooms": 4,
        "bedrooms": 3,
        "rent": 1450,
        "surface": 88,
        "active": True,
        "images": [],
    }
    listing.update(overrides)
    return listing


@pytest.mark.parametrize("reference", ["GES10980017-495", "GES10220016-542"])
def test_real_citya_bienici_agency_reference_collapses_to_one_traceable_card(reference: str) -> None:
    citya = _listing(
        f"citya:{reference}",
        "citya",
        reference,
        title="Location appartement 4 pièces",
        description="Annonce Citya concise.",
        images=["/thumbs/citya.jpg"],
        parking=True,
    )
    bienici = _listing(
        f"bienici:ag971031-{reference}",
        "bienici",
        reference,
        url=f"https://www.bienici.com/annonce/ag971031-{reference}",
        description=(
            "Appartement familial de trois chambres avec une grande varangue. "
            f"Référence agence : {reference}."
        ),
        images=["/thumbs/bienici.jpg"],
        elevator=True,
    )

    visible, report = deduplicate_public_feed([citya, bienici])

    assert len(visible) == 1
    assert report["hidden_duplicates"] == 1
    assert visible[0]["dedup_reason"] == "same_business_reference"
    assert {link["id"] for link in visible[0]["also_on"]} == {citya["id"], bienici["id"]}
    assert visible[0]["seen_also_on"] == ["bienici", "citya"]
    assert visible[0]["images"] == ["/thumbs/bienici.jpg", "/thumbs/citya.jpg"]
    assert visible[0]["parking"] is True
    assert visible[0]["elevator"] is True


@pytest.mark.parametrize(
    ("left_changes", "right_changes"),
    [
        ({"address": "1 rue des Manguiers"}, {"address": "9 rue des Manguiers"}),
        ({"floor": 1}, {"floor": 3}),
        ({"rooms": 3}, {"rooms": 4}),
    ],
)
def test_same_agency_reference_never_overrides_property_contradiction(
    left_changes: dict, right_changes: dict
) -> None:
    reference = "GES10980017-495"
    citya = _listing(f"citya:{reference}", "citya", reference, **left_changes)
    bienici = _listing(
        "bienici:ag971031-474984932",
        "bienici",
        reference,
        **right_changes,
    )

    visible, report = deduplicate_public_feed([citya, bienici])

    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0


def test_ges_like_tokens_that_differ_do_not_merge_on_metrics_alone() -> None:
    citya = _listing("citya:GES10980017-495", "citya", "GES10980017-495")
    bienici = _listing("bienici:474984932", "bienici", "GES10980017-496")

    visible, report = deduplicate_public_feed([citya, bienici])

    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0
