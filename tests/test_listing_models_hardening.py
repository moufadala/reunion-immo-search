from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.listing_models import NormalizedListing, PublishedListing

NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)

def _published(**overrides) -> PublishedListing:
    values = {
        "id": "leboncoin:123", "source": "leboncoin", "source_id": "123",
        "url": "https://www.leboncoin.fr/ad/123", "canonical_url": "https://www.leboncoin.fr/ad/123",
        "title": "Appartement T3", "city": "Saint-Denis", "district": "Centre",
        "description": "Description intégrale", "property_type": "apartment",
        "price_eur": 900, "charges_eur": 50, "surface_m2": 65.5, "rooms": 3,
        "bedrooms": 2, "published_at": NOW, "image_urls": ["https://img.test/1.jpg"],
        "status": "active", "first_seen_at": NOW, "last_seen_at": NOW,
    }
    values.update(overrides)
    return PublishedListing(**values)

def test_published_listing_keeps_all_normalized_business_fields() -> None:
    listing = _published()
    for field in NormalizedListing.model_fields:
        if field not in {"collected_at", "raw_ref"}:
            assert field in PublishedListing.model_fields
    assert listing.price_eur == 900
    assert listing.surface_m2 == 65.5
    assert listing.description == "Description intégrale"

def test_removed_at_cannot_predate_last_seen_at() -> None:
    with pytest.raises(ValidationError):
        _published(status="removed", last_seen_at=NOW, removed_at=NOW - timedelta(seconds=1))

def test_models_reject_naive_dates() -> None:
    with pytest.raises(ValidationError):
        _published(first_seen_at=datetime(2026, 8, 14), last_seen_at=datetime(2026, 8, 14))

def test_assignment_cannot_break_an_existing_invariant() -> None:
    listing = _published()
    with pytest.raises(ValidationError):
        listing.price_eur = -1
