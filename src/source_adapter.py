"""Small common contract implemented progressively by source adapters."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from src.listing_models import NormalizedListing, RawListing


@runtime_checkable
class ListingAdapter(Protocol):
    """Minimum common surface; optional capabilities belong in other protocols."""

    source: str

    def collect(self) -> Iterable[RawListing]: ...

    def normalize(self, raw: RawListing) -> NormalizedListing: ...
