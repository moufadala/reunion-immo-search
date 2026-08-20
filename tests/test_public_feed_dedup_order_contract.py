from pathlib import Path

from src.public_feed_dedup import deduplicate_public_feed


def test_active_duplicate_wins_over_richer_inactive_record() -> None:
    common = {
        "commune": "Saint-Denis", "type": "Appartement", "rooms": 3,
        "rent": 1100, "surface": 70, "address": "4 rue des Flamboyants",
        "images": ["unique.jpg"],
        "title": "Appartement - référence agence GES12345678-123",
    }
    inactive = {**common, "id": "a:old", "source": "a", "url": "https://a/old",
                "active": False, "description": "Très détaillée " * 100}
    active = {**common, "id": "b:new", "source": "b", "url": "https://b/new",
              "active": True, "description": "Courte"}

    visible, _ = deduplicate_public_feed([inactive, active])

    assert visible[0]["id"] == "b:new"


def test_export_filters_active_before_deduplicating_cards() -> None:
    source = (Path(__file__).parents[1] / "scripts" / "export_feed.py").read_text(
        encoding="utf-8"
    )
    assert source.index("listings = active_public_listings(listings)") < source.index(
        "listings, dedup_report = deduplicate_public_feed("
    )
