from pathlib import Path

from src.public_feed_dedup import deduplicate_public_feed
JPEG = b"\xff\xd8\xff"


def test_public_feed_engine_canonicalizes_binary_twins_when_photo_root_is_supplied(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "thumbs").mkdir(parents=True)
    (app / "thumbs" / "a.jpg").write_bytes(JPEG + b"same")
    (app / "thumbs" / "b.jpg").write_bytes(JPEG + b"same")
    item = {
        "id": "portal:1",
        "source": "portal",
        "url": "https://portal.test/1",
        "commune": "Saint-Denis",
        "type": "Appartement",
        "rent": 1200,
        "surface": 70,
        "image": "/thumbs/a.jpg",
        "images": ["/thumbs/a.jpg", "/thumbs/b.jpg"],
    }

    visible, _ = deduplicate_public_feed([item], photo_root=app)

    assert visible[0]["image"] == "/thumbs/a.jpg"
    assert visible[0]["images"] == ["/thumbs/a.jpg"]


def test_duplicate_merge_keeps_distinct_photos_and_complementary_fields(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "thumbs").mkdir(parents=True)
    (app / "thumbs" / "primary.jpg").write_bytes(JPEG + b"primary")
    (app / "thumbs" / "binary-twin.jpg").write_bytes(JPEG + b"primary")
    (app / "thumbs" / "other.jpg").write_bytes(JPEG + b"other")
    common = {
        "commune": "Saint-Denis",
        "type": "Appartement",
        "rooms": 4,
        "rent": 1450,
        "surface": 88,
        "address": "4 rue des Flamboyants",
        "active": True,
    }
    first = {
        **common,
        "id": "citya:GES10980017-495",
        "source": "citya",
        "url": "https://citya.test/GES10980017-495",
        "description": "Description longue " * 20,
        "image": "/thumbs/primary.jpg",
        "images": ["/thumbs/primary.jpg"],
        "parking": True,
    }
    second = {
        **common,
        "id": "bienici:474984932",
        "source": "bienici",
        "url": "https://bienici.test/474984932",
        "description": "Référence agence : GES10980017-495.",
        "image": "/thumbs/binary-twin.jpg",
        "images": ["/thumbs/binary-twin.jpg", "/thumbs/other.jpg"],
        "elevator": True,
    }

    visible, report = deduplicate_public_feed([first, second], photo_root=app)

    assert report["hidden_duplicates"] == 1
    assert len(visible) == 1
    assert visible[0]["images"] == ["/thumbs/primary.jpg", "/thumbs/other.jpg"]
    assert visible[0]["parking"] is True
    assert visible[0]["elevator"] is True
    assert {link["id"] for link in visible[0]["also_on"]} == {first["id"], second["id"]}
