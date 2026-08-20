from pathlib import Path

from src.public_feed_dedup import deduplicate_public_feed


ROOT = Path(__file__).parents[1]


def test_shared_photo_without_exact_address_does_not_hide_a_listing() -> None:
    common = {"commune": "Saint-Denis", "type": "Appartement", "rooms": 3,
              "rent": 1100, "surface": 70, "images": ["facade-placeholder.jpg"]}
    visible, report = deduplicate_public_feed([
        {**common, "id": "a:1", "url": "https://a/1", "floor": None},
        {**common, "id": "b:2", "url": "https://b/2", "floor": None},
    ])
    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0

def test_same_building_address_without_second_proof_stays_visible() -> None:
    common = {"commune": "Saint-Denis", "type": "Appartement", "rooms": 3,
              "rent": 1100, "surface": 70, "address": "4 rue des Flamboyants",
              "images": []}
    visible, report = deduplicate_public_feed([
        {**common, "id": "a:1", "url": "https://a/1"},
        {**common, "id": "b:2", "url": "https://b/2"},
    ])
    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0



def test_event_feed_applies_policy_and_visible_count_after_dedup() -> None:
    source = (ROOT / "scripts" / "export_feed.py").read_text(encoding="utf-8")
    assert "if not evaluate_publication(item).eligible:" in source
    assert source.index("listings, dedup_report = deduplicate_public_feed(") < source.index(
        "movements['actives'] = len(listings)"
    )


def test_detail_upsert_rejects_blank_location_fields() -> None:
    source = (ROOT / "scripts" / "detail_enrich.py").read_text(encoding="utf-8")
    for field in ("address", "street", "residence", "postal_code", "locality", "floor"):
        assert f"{field}=coalesce(nullif(trim(excluded.{field}), '')" in source

def test_detail_upsert_preserves_geo_quality_sentinels() -> None:
    source = (ROOT / "scripts" / "detail_enrich.py").read_text(encoding="utf-8").lower()
    assert "in ('', 'inconnu') then listing_detail.precision" in source
    assert "in ('', 'aucun', 'inconnu')" in source


def test_duplicate_merge_function_is_present() -> None:
    assert "_merge_complementary" in (ROOT / "src" / "public_feed_dedup.py").read_text(encoding="utf-8")
