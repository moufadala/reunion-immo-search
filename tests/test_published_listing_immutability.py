from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.listing_models import PublishedListing

def test_published_history_changes_require_atomic_reconstruction() -> None:
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    listing = PublishedListing(id="source:1", source="source", source_id="1", url="https://example.test/1", title="T2", city="Saint-Denis", status="active", first_seen_at=now, last_seen_at=now)
    with pytest.raises(ValidationError):
        listing.status = "removed"
    assert listing.status == "active"
    assert listing.removed_at is None
