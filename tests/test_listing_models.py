from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.listing_models import NormalizedListing, PublishedListing, RawListing

NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)

def test_raw_listing_preserves_the_complete_source_payload() -> None:
    payload = {"description": "Texte source", "photos": ["a.jpg", "b.jpg"], "unknown": {"x": 1}}
    listing = RawListing(source="leboncoin", source_id="123", url="https://www.leboncoin.fr/ad/123", collected_at=NOW, payload=payload)
    assert listing.payload == payload

def test_normalized_listing_keeps_description_and_all_unique_photos() -> None:
    description = "Une description longue. " * 2_000
    listing = NormalizedListing(source="leboncoin", source_id="123", url="https://www.leboncoin.fr/ad/123", title="Appartement T3", city="Saint-Denis", description=description, image_urls=["https://img.test/1.jpg", "https://img.test/2.jpg", "https://img.test/1.jpg"], collected_at=NOW)
    assert listing.description == description
    assert listing.image_urls == ["https://img.test/1.jpg", "https://img.test/2.jpg"]

@pytest.mark.parametrize("field", ["source", "source_id", "url", "title", "city"])
def test_normalized_listing_rejects_blank_identity_fields(field: str) -> None:
    values = {"source": "leboncoin", "source_id": "123", "url": "https://www.leboncoin.fr/ad/123", "title": "Appartement T3", "city": "Saint-Denis", "collected_at": NOW}
    values[field] = "   "
    with pytest.raises(ValidationError):
        NormalizedListing(**values)

def test_normalized_listing_rejects_impossible_numbers() -> None:
    with pytest.raises(ValidationError):
        NormalizedListing(source="leboncoin", source_id="123", url="https://www.leboncoin.fr/ad/123", title="Appartement T3", city="Saint-Denis", price_eur=-1, surface_m2=0, collected_at=NOW)

def test_published_listing_enforces_removed_history_consistency() -> None:
    with pytest.raises(ValidationError):
        PublishedListing(id="leboncoin:123", source="leboncoin", source_id="123", url="https://www.leboncoin.fr/ad/123", title="Appartement T3", city="Saint-Denis", status="removed", first_seen_at=NOW, last_seen_at=NOW)

def test_models_serialize_with_an_explicit_stable_schema_version() -> None:
    listing = RawListing(source="leboncoin", source_id="123", url="https://www.leboncoin.fr/ad/123", collected_at=NOW, payload={})
    assert listing.model_dump(mode="json")["schema_version"] == "1"
