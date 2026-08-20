import pytest

from src.public_feed_dedup import deduplicate_public_feed


@pytest.mark.parametrize(
    ("field", "left_value", "right_value"),
    (
        ("address", "4 rue des Flamboyants", "8 rue des Flamboyants"),
        ("floor", 1, 3),
        ("rooms", 3, 4),
        ("furnished", "Non meublé", "Meublé"),
    ),
)
def test_same_url_never_hides_a_hard_contradiction(
    field: str,
    left_value: object,
    right_value: object,
) -> None:
    common = {
        "commune": "Saint-Denis",
        "type": "Appartement",
        "rent": 1100,
        "surface": 70,
        "title": "Appartement résidence Les Flamboyants",
        "images": [],
        "url": "https://portal.test/annonce-123",
    }
    left = {**common, "id": "portal:a", "source": "portal", field: left_value}
    right = {**common, "id": "portal:b", "source": "portal", field: right_value}

    visible, report = deduplicate_public_feed([left, right])

    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0


def test_same_residence_can_contain_two_distinct_apartments() -> None:
    common = {"commune": "Saint-Denis", "type": "Appartement", "rooms": 3, "rent": 1100, "surface": 70, "residence": "Les Flamboyants", "images": []}
    a = {**common, "id": "a:1", "source": "a", "url": "https://a/1", "title": "Appartement T3", "description": "Lot du premier étage", "floor": 1, "bedrooms": 2}
    b = {**common, "id": "b:2", "source": "b", "url": "https://b/2", "title": "Appartement T3", "description": "Lot du troisième étage", "floor": 3, "bedrooms": 2}
    visible, report = deduplicate_public_feed([a, b])
    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0


def test_same_address_and_facade_photo_do_not_hide_distinct_apartments() -> None:
    common = {
        "commune": "Saint-Denis",
        "type": "Appartement",
        "rooms": 3,
        "rent": 1100,
        "surface": 70,
        "address": "4 rue des Flamboyants",
        "residence": "Les Flamboyants",
        "images": ["facade-commune.jpg"],
        "title": "Appartement T3 résidence Les Flamboyants",
    }
    a = {
        **common,
        "id": "a:lot-12",
        "source": "a",
        "url": "https://a/lot-12",
        "description": "Appartement du premier étage donnant sur le jardin, avec cuisine rouge et parking numéro 12.",
    }
    b = {
        **common,
        "id": "b:lot-37",
        "source": "b",
        "url": "https://b/lot-37",
        "description": "Appartement du troisième étage côté rue, avec cuisine blanche et parking numéro 37.",
    }

    visible, report = deduplicate_public_feed([a, b])

    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0



def test_semantically_distinct_url_paths_keep_their_separators_and_stay_visible() -> None:
    common = {
        "commune": "Saint-Denis",
        "type": "Appartement",
        "rent": 1100,
        "surface": 70,
        "images": [],
    }
    left = {
        **common,
        "id": "portal:a",
        "source": "portal",
        "url": "https://portal.test/annonce/12-34?utm_source=mail",
    }
    right = {
        **common,
        "id": "portal:b",
        "source": "portal",
        "url": "https://portal.test/annonce/12/34?utm_source=mail",
    }

    visible, report = deduplicate_public_feed([left, right])

    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0


def test_same_url_normalizes_host_case_trailing_slash_and_tracking_query() -> None:
    common = {
        "commune": "Saint-Denis",
        "type": "Appartement",
        "rent": 1100,
        "surface": 70,
        "images": [],
    }
    left = {
        **common,
        "id": "portal:a",
        "source": "portal",
        "url": "HTTPS://PORTAL.TEST/annonce/12-34/?utm_source=mail&gclid=abc",
    }
    right = {
        **common,
        "id": "portal:b",
        "source": "portal",
        "url": "https://portal.test/annonce/12-34",
    }

    visible, report = deduplicate_public_feed([left, right])

    assert len(visible) == 1
    assert report["hidden_duplicates"] == 1
    assert visible[0]["dedup_reason"] == "same_url"

def test_duplicate_group_is_traceable() -> None:
    description = "Appartement unique avec grande varangue, cuisine ouverte, parking et vue mer dans une petite copropriété. " * 2
    common = {"commune": "Saint-Denis", "type": "Appartement", "rooms": 3, "rent": 1100, "surface": 70, "address": "4 rue des Flamboyants", "title": "Résidence Flamboyants appartement varangue vue mer", "description": description, "images": ["unique.jpg"]}
    visible, report = deduplicate_public_feed([
        {**common, "id": "a:1", "source": "a", "url": "https://a/1"},
        {**common, "id": "b:2", "source": "b", "url": "https://b/2"},
    ])
    assert report["groups"] == 1
    assert visible[0]["dedup_group_id"]
    assert visible[0]["canonical_id"] == visible[0]["id"]
    assert visible[0]["dedup_reason"] == "same_title_and_description"
