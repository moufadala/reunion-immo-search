from copy import deepcopy
from pathlib import Path

from src.photo_gallery import audit_public_galleries
WEBP = b"RIFF\x00\x00\x00\x00WEBP"


def _listing(source: str, source_id: str, image: str = "/thumbs/shared.webp") -> dict:
    return {
        "id": f"{source}:{source_id}",
        "source": source,
        "image": image,
        "images": [image],
    }


def test_five_distinct_business_ids_sharing_one_source_photo_is_blocking_but_non_mutating(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "thumbs").mkdir(parents=True)
    (app / "thumbs" / "shared.webp").write_bytes(WEBP + b"citya-placeholder")
    listings = [
        _listing("citya", source_id)
        for source_id in (
            "GES27390302-542",
            "GES56851227-542",
            "GES10980017-495",
            "GES10220016-542",
            "GES05110025-495",
        )
    ]
    before = deepcopy(listings)

    report = audit_public_galleries(listings, app_root=app)

    assert report["ok"] is False
    assert report["galleries_with_content_duplicates"] == 0
    assert report["shared_placeholder_suspicions"] == 1
    suspicion = report["suspected_shared_placeholders"][0]
    assert suspicion["source"] == "citya"
    assert suspicion["sources"] == ["citya"]
    assert suspicion["listing_count"] == 5
    assert suspicion["business_ids"] == sorted(item["id"].split(":", 1)[1] for item in listings)
    assert suspicion["paths"] == ["/thumbs/shared.webp"]
    assert listings == before


def test_shared_photo_below_five_distinct_ids_stays_visible_without_false_block(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "thumbs").mkdir(parents=True)
    (app / "thumbs" / "shared.webp").write_bytes(WEBP + b"possible-residence-photo")
    listings = [_listing("citya", f"GES0000000{i}-542") for i in range(4)]

    report = audit_public_galleries(listings, app_root=app)

    assert report["ok"] is True
    assert report["shared_placeholder_suspicions"] == 0


def test_shared_hash_across_sources_is_blocking_at_five_distinct_listing_ids(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "thumbs").mkdir(parents=True)
    (app / "thumbs" / "shared.webp").write_bytes(WEBP + b"same-building-photo")
    listings = [
        *[_listing("citya", f"GES0000000{i}-542") for i in range(3)],
        *[_listing("bienici", f"ag971031-{i}") for i in range(3)],
    ]
    before = deepcopy(listings)

    report = audit_public_galleries(listings, app_root=app)

    assert report["ok"] is False
    assert report["shared_placeholder_suspicions"] == 1
    suspicion = report["suspected_shared_placeholders"][0]
    assert suspicion["source"] is None
    assert suspicion["sources"] == ["bienici", "citya"]
    assert suspicion["listing_count"] == 6
    assert suspicion["listing_ids"] == sorted(item["id"] for item in listings)
    assert suspicion["paths"] == ["/thumbs/shared.webp"]
    assert listings == before
