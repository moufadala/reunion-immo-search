from __future__ import annotations

from datetime import datetime, timezone

from src.listing_models import NormalizedListing, RawListing
from src.source_adapter import ListingAdapter

class ExampleAdapter:
    source = "example"
    def collect(self):
        return [RawListing(source=self.source, source_id="1", url="https://example.test/1", collected_at=datetime.now(timezone.utc), payload={"title": "T2", "city": "Saint-Denis"})]
    def normalize(self, raw: RawListing) -> NormalizedListing:
        return NormalizedListing(source=raw.source, source_id=raw.source_id, url=raw.url, title=raw.payload["title"], city=raw.payload["city"], collected_at=raw.collected_at)

def test_adapter_protocol_is_runtime_checkable() -> None:
    adapter = ExampleAdapter()
    assert isinstance(adapter, ListingAdapter)
    raw = list(adapter.collect())
    assert [adapter.normalize(item).source_id for item in raw] == ["1"]
