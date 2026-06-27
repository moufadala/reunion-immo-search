#!/usr/bin/env python3
"""Canonical listing contracts for Immo Réunion.

Pure helpers only: no DB, no network, no file mutation. These functions define
how historical technical scraper fields map to the public listing payload.
"""
from __future__ import annotations

from typing import Any, TypedDict


class PublicListing(TypedDict, total=False):
    id: str
    source: str
    source_id: str
    url: str
    price: int | float | None
    surface: int | float | None
    type: str
    city: str
    district: str
    location: str
    region: str
    rooms: int | float | None
    bedrooms: int | float | None
    furnished: str
    image_url: str
    local_image_url: str
    image_urls: list[str]
    local_image_urls: list[str]
    display_canonical: bool
    canonical_display_id: str


PUBLIC_LISTING_KEY_FIELDS: tuple[str, ...] = (
    "id",
    "source",
    "source_id",
    "url",
    "price",
    "surface",
    "type",
    "city",
    "district",
    "location",
    "region",
    "rooms",
    "bedrooms",
    "furnished",
    "image_url",
    "local_image_url",
    "image_urls",
    "local_image_urls",
    "display_canonical",
    "canonical_display_id",
)


def first_present(row: dict[str, Any], *keys: str) -> Any:
    """Return first non-empty value, preserving explicit False/0."""
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if value is False or value == 0:
            return value
        if value not in (None, "", [], {}):
            return value
    return None


def number_or_none(value: Any) -> int | float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace("\u202f", " ").replace("€", "").replace("m²", "")
    text = text.replace(" ", "").replace(",", ".")
    try:
        num = float(text)
    except ValueError:
        return None
    return int(num) if num.is_integer() else num


def string_or_empty(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def list_of_strings(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x or "").strip()]
    return [str(value).strip()]


def normalize_public_listing(row: dict[str, Any]) -> PublicListing:
    """Normalize one technical or public row to the public listing contract."""
    source = first_present(row, "source", "source_site")
    url = first_present(row, "url", "source_url")
    price = number_or_none(first_present(row, "price", "rent_eur"))
    surface = number_or_none(first_present(row, "surface", "surface_m2"))
    typ = first_present(row, "type", "property_type", "property_type_normalized")
    city = first_present(row, "city", "commune")
    display_canonical = row.get("display_canonical")
    if display_canonical is None:
        display_canonical = True

    local_images = list_of_strings(first_present(row, "local_image_urls"))
    primary_local = string_or_empty(first_present(row, "local_image_url"))
    if primary_local and primary_local not in local_images:
        local_images.insert(0, primary_local)

    images = list_of_strings(first_present(row, "image_urls"))
    primary_image = string_or_empty(first_present(row, "image_url"))
    if primary_image and primary_image not in images:
        images.insert(0, primary_image)

    out: PublicListing = {
        "id": string_or_empty(first_present(row, "id")),
        "source": string_or_empty(source),
        "source_id": string_or_empty(first_present(row, "source_id")),
        "url": string_or_empty(url),
        "price": price,
        "surface": surface,
        "type": string_or_empty(typ),
        "city": string_or_empty(city),
        "district": string_or_empty(first_present(row, "district")),
        "location": string_or_empty(first_present(row, "location", "location_label", "primary_zone")),
        "region": string_or_empty(first_present(row, "region")),
        "rooms": number_or_none(first_present(row, "rooms")),
        "bedrooms": number_or_none(first_present(row, "bedrooms")),
        "furnished": string_or_empty(first_present(row, "furnished")),
        "image_url": primary_image,
        "local_image_url": primary_local,
        "image_urls": images,
        "local_image_urls": local_images,
        "display_canonical": bool(display_canonical),
        "canonical_display_id": string_or_empty(first_present(row, "canonical_display_id")),
    }
    return out


def public_listing_key_fields() -> tuple[str, ...]:
    return PUBLIC_LISTING_KEY_FIELDS
