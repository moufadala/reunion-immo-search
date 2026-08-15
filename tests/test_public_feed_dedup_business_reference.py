from src.public_feed_dedup import deduplicate_public_feed


def _real_run_pair() -> list[dict]:
    common = {
        "commune": "Sainte-Marie",
        "type": "Maison",
        "rooms": 5,
        "rent": 1600,
        "surface": 144,
        "active": True,
    }
    return [
        {
            **common,
            "id": "ofim_rss:73279",
            "source": "ofim_rss",
            "url": "https://www.ofim-reunion.fr/location/73279",
            "title": "Location Maison / Villa SAINTE MARIE - Terrain Elisa",
            "description": (
                "OFIM propose cette villa de standing de 144m2 à Terrain Elisa. "
                "Quatre chambres, terrasse et jardin, disponible le 1er septembre 2026. "
                "Loyer mensuel de 1600 euros."
            ),
            "image": "/thumbs/5c48a38856ea57597759f810.jpg",
            "image_locale": True,
            "images": ["/thumbs/5c48a38856ea57597759f810.jpg"],
        },
        {
            **common,
            "id": "leboncoin:3243892983",
            "source": "leboncoin",
            "url": "https://www.leboncoin.fr/ad/locations/3243892983",
            "title": "Villa 5 pièces 144 m²",
            "description": (
                "A louer villa de 4 chambres à Sainte-Marie Terrain Elisa. "
                "OFIM propose cette villa de standing de 144m2. Quatre chambres, terrasse "
                "et jardin, disponible le 1er septembre 2026. Loyer mensuel de 1600 euros. "
                "Référence annonce : 18A73279."
            ),
            "image": None,
            "image_locale": False,
            "images": [],
        },
    ]


def test_real_run_153444_business_reference_merges_ofim_photo_into_leboncoin() -> None:
    visible, report = deduplicate_public_feed(_real_run_pair())

    assert report["hidden_duplicates"] == 1
    assert len(visible) == 1
    assert visible[0]["dedup_reason"] == "same_business_reference"
    assert visible[0]["image"] == "/thumbs/5c48a38856ea57597759f810.jpg"
    assert visible[0]["image_locale"] is True
    assert visible[0]["images"] == ["/thumbs/5c48a38856ea57597759f810.jpg"]


def test_generic_matching_digits_are_not_a_business_reference() -> None:
    first, second = _real_run_pair()
    second["description"] = (
        "Villa semblable près du code postal 73279, quatre chambres, terrasse et jardin."
    )

    visible, report = deduplicate_public_feed([first, second])

    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0


def test_business_reference_never_overrides_a_hard_property_contradiction() -> None:
    first, second = _real_run_pair()
    second["rooms"] = 3

    visible, report = deduplicate_public_feed([first, second])

    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0

def test_conflicting_explicit_business_references_never_merge() -> None:
    first, second = _real_run_pair()
    first["description"] += " Référence annonce : 99B73279."

    visible, report = deduplicate_public_feed([first, second])

    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0
