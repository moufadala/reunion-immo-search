from src.public_feed_dedup import deduplicate_public_feed

def test_same_residence_can_contain_two_distinct_apartments() -> None:
    common = {"commune": "Saint-Denis", "type": "Appartement", "rooms": 3, "rent": 1100, "surface": 70, "residence": "Les Flamboyants", "images": []}
    a = {**common, "id": "a:1", "source": "a", "url": "https://a/1", "title": "Appartement T3", "description": "Lot du premier étage", "floor": 1, "bedrooms": 2}
    b = {**common, "id": "b:2", "source": "b", "url": "https://b/2", "title": "Appartement T3", "description": "Lot du troisième étage", "floor": 3, "bedrooms": 2}
    visible, report = deduplicate_public_feed([a, b])
    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0

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
    assert visible[0]["dedup_reason"] == "same_address_and_photo"
