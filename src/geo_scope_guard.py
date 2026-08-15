"""Conservative last-resort guard for manifest geographic contradictions.

This module deliberately recognises only a small reviewed list.  It must not
become a second geocoder: unknown or ambiguous text remains publishable and is
left to the regular location intelligence pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Mapping, Any


@dataclass(frozen=True)
class OutsideScopeEvidence:
    locality: str
    field: str
    matched_text: str


_FIELDS = ("location_label", "address", "quartier", "title", "description")
_PROXIMITY_PREFIX = re.compile(
    r"(?:proche\s+de|a\s+proximite\s+de|a\s+\d+(?:[.,]\d+)?\s*km\s+de)"
    r"\s+(?:(?:la|le|les|l)\s+)?$"
)


def _normalise(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _is_proximity_mention(text: str, match_start: int) -> bool:
    return bool(_PROXIMITY_PREFIX.search(text[max(0, match_start - 35) : match_start]))


def _find(text: str, phrase: str) -> int | None:
    match = re.search(rf"\b{re.escape(phrase)}\b", text)
    return match.start() if match else None


def manifest_outside_scope(listing: Mapping[str, Any]) -> OutsideScopeEvidence | None:
    """Return evidence only for an explicit, reviewed outside-scope locality."""

    for field in _FIELDS:
        text = _normalise(listing.get(field))
        if not text:
            continue

        candidates = (
            ("La Saline-les-Hauts", "la saline les hauts"),
            ("Plaine-des-Cafres", "plaine des cafres"),
        )
        for locality, phrase in candidates:
            start = _find(text, phrase)
            if start is not None and not _is_proximity_mention(text, start):
                return OutsideScopeEvidence(locality, field, phrase)

        river = _find(text, "la riviere")
        saint_louis = _find(text, "saint louis")
        if river is not None and saint_louis is not None:
            first = min(river, saint_louis)
            if not _is_proximity_mention(text, first):
                return OutsideScopeEvidence("La Riviere / Saint-Louis", field, "la riviere saint louis")

    return None
