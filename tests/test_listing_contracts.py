#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.immo_contracts import normalize_public_listing, public_listing_key_fields
from scripts.slim_public_listings import preserve_local_gallery


def test_maps_technical_fields_to_public_contract() -> None:
    row = {
        "id": "zimo:1",
        "source_site": "zimo",
        "source_id": "1",
        "source_url": "https://example.test/1",
        "rent_eur": "900",
        "surface_m2": "65.5",
        "property_type": "Appartement",
        "commune": "Saint-Denis",
    }

    out = normalize_public_listing(row)

    assert out["id"] == "zimo:1"
    assert out["source"] == "zimo"
    assert out["source_id"] == "1"
    assert out["url"] == "https://example.test/1"
    assert out["price"] == 900
    assert out["surface"] == 65.5
    assert out["type"] == "Appartement"
    assert out["city"] == "Saint-Denis"
    assert out["display_canonical"] is True


def test_preserves_non_destructive_canonical_display_policy() -> None:
    row = {
        "id": "seloger:2",
        "source": "seloger",
        "url": "https://example.test/2",
        "price": 910,
        "surface": 66,
        "type": "Appartement",
        "display_canonical": False,
        "canonical_display_id": "zimo:1",
    }

    out = normalize_public_listing(row)

    assert out["display_canonical"] is False
    assert out["canonical_display_id"] == "zimo:1"
    assert out["source"] == "seloger"


def test_expands_primary_photos_into_gallery_lists() -> None:
    row = {
        "id": "locamoi:3",
        "source_site": "locamoi",
        "url": "https://example.test/3",
        "local_image_url": "thumbs/a.webp",
        "image_url": "https://img.example/a.jpg",
    }

    out = normalize_public_listing(row)

    assert out["local_image_urls"] == ["thumbs/a.webp"]
    assert out["image_urls"] == ["https://img.example/a.jpg"]


def test_public_listing_key_fields_are_stable() -> None:
    keys = public_listing_key_fields()
    for key in ["id", "source", "url", "price", "surface", "type", "display_canonical"]:
        assert key in keys


def test_public_slimming_preserves_every_local_gallery_photo() -> None:
    photos = [f"thumbs/photo-{i}.webp" for i in range(20)]
    assert preserve_local_gallery(photos) == photos


def main() -> int:
    tests = [
        test_maps_technical_fields_to_public_contract,
        test_preserves_non_destructive_canonical_display_policy,
        test_expands_primary_photos_into_gallery_lists,
        test_public_slimming_preserves_every_local_gallery_photo,
        test_public_listing_key_fields_are_stable,
    ]
    for test in tests:
        test()
    print("LISTING_CONTRACTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
