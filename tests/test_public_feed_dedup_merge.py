from src.public_feed_dedup import deduplicate_public_feed


def test_duplicate_canonical_merges_gallery_and_missing_fields() -> None:
    common = {
        "commune": "Saint-Denis", "type": "Appartement", "rooms": 3,
        "rent": 1100, "surface": 70, "address": "4 rue des Flamboyants",
        "active": True,
    }
    detailed = {**common, "id": "a:1", "url": "https://a/1",
                "description": "Description complète " * 20,
                "images": ["shared.jpg"], "bedrooms": None}
    gallery = {**common, "id": "b:2", "url": "https://b/2",
               "description": "Courte", "images": ["shared.jpg", "2.jpg", "3.jpg"],
               "bedrooms": 2}

    visible, report = deduplicate_public_feed([detailed, gallery])

    assert report["hidden_duplicates"] == 1
    assert visible[0]["bedrooms"] == 2
    assert visible[0]["images"] == ["shared.jpg", "2.jpg", "3.jpg"]
