"""Versioned listing models at the three pipeline boundaries.

These models are intentionally additive: existing scrapers and the public
``TypedDict`` contract keep working while adapters migrate source by source.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1"
MODEL_CONFIG = ConfigDict(extra="forbid", validate_assignment=True)

def _non_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value

def _unique_non_blank(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))

def _timezone_aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("datetime must include a timezone")
    return value

class RawListing(BaseModel):
    """Lossless source response plus the minimum collection metadata.

    A source identifier may be absent at collection time. Its adapter must then
    derive a stable identifier (usually from the canonical URL) or reject the
    row before creating ``NormalizedListing``.
    """
    model_config = MODEL_CONFIG
    schema_version: Literal["1"] = SCHEMA_VERSION
    source: str
    source_id: str | None = None
    url: str
    collected_at: datetime
    payload: dict[str, Any]
    _validate_source = field_validator("source")(_non_blank)
    _validate_url = field_validator("url")(_non_blank)
    _validate_collected_at = field_validator("collected_at")(_timezone_aware)

    @field_validator("source_id")
    @classmethod
    def validate_optional_source_id(cls, value: str | None) -> str | None:
        return _non_blank(value) if value is not None else None

class _ListingFields(BaseModel):
    """Fields that must survive normalization through publication."""
    model_config = MODEL_CONFIG
    schema_version: Literal["1"] = SCHEMA_VERSION
    source: str
    source_id: str
    url: str
    canonical_url: str | None = None
    title: str
    city: str
    district: str | None = None
    description: str | None = None
    property_type: str | None = None
    price_eur: int | None = Field(default=None, gt=0)
    charges_eur: int | None = Field(default=None, ge=0)
    surface_m2: float | None = Field(default=None, gt=0)
    rooms: int | None = Field(default=None, gt=0)
    bedrooms: int | None = Field(default=None, ge=0)
    agency_or_owner: str | None = None
    published_at: datetime | None = None
    image_urls: list[str] = Field(default_factory=list)
    _validate_identity = field_validator("source", "source_id", "url", "title", "city")(_non_blank)
    _validate_published_at = field_validator("published_at")(_timezone_aware)

    @field_validator("image_urls")
    @classmethod
    def deduplicate_images(cls, values: list[str]) -> list[str]:
        return _unique_non_blank(values)

class NormalizedListing(_ListingFields):
    """Source-independent listing, before persistence and publication."""
    collected_at: datetime
    raw_ref: str | None = None
    _validate_collected_at = field_validator("collected_at")(_timezone_aware)

class PublishedListing(_ListingFields):
    """Complete listing plus stable public/history metadata."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    status: Literal["active", "inactive", "removed"]
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None = None
    local_image_urls: list[str] = Field(default_factory=list)
    _validate_id = field_validator("id")(_non_blank)
    _validate_history_dates = field_validator("first_seen_at", "last_seen_at", "removed_at")(_timezone_aware)

    @field_validator("local_image_urls")
    @classmethod
    def deduplicate_local_images(cls, values: list[str]) -> list[str]:
        return _unique_non_blank(values)

    @model_validator(mode="after")
    def validate_history(self) -> "PublishedListing":
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at must be on or after first_seen_at")
        if self.status == "removed" and self.removed_at is None:
            raise ValueError("removed listings require removed_at")
        if self.removed_at is not None and self.removed_at < self.last_seen_at:
            raise ValueError("removed_at must be on or after last_seen_at")
        if self.status == "active" and self.removed_at is not None:
            raise ValueError("active listings cannot have removed_at")
        return self
