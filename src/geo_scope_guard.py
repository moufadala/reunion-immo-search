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


_FIELDS = (
    "commune", "city", "location_label", "address", "quartier", "district",
    "title", "url", "description",
)
_OUTSIDE_LOCALITIES = (
    ("La Saline-les-Hauts", "la saline les hauts"),
    ("Plaine-des-Cafres", "plaine des cafres"),
    ("Saint-Gilles les Bains", "saint gilles les bains"),
    ("Plaine-des-Palmistes", "plaine des palmistes"),
    ("Saint-André", "saint andre"),
    ("Les Avirons", "avirons"),
    ("Sainte-Suzanne", "sainte suzanne"),
    ("Saint-Philippe", "saint philippe"),
    ("Le Tévelave", "tevelave"),
    ("Le Tampon", "le tampon"),
    ("Saint-Pierre", "saint pierre"),
    ("Saint-Paul", "saint paul"),
    ("La Possession", "la possession"),
    ("Saint-Leu", "saint leu"),
    ("Saint-Louis", "saint louis"),
    ("Le Port", "le port"),
    ("Saint-Benoît", "saint benoit"),
    ("Saint-Joseph", "saint joseph"),
    ("Bras-Panon", "bras panon"),
    ("Entre-Deux", "entre deux"),
    ("Étang-Salé", "etang sale"),
    ("La Saline", "la saline"),
    ("Petite Île", "petite ile"),
    ("Cilaos", "cilaos"),
    ("Salazie", "salazie"),
    ("Trois-Bassins", "trois bassins"),
)
_PROXIMITY_PREFIX = re.compile(
    r"(?:proche\s+d(?:e|u|es)|a\s+proximite\s+d(?:e|u|es)|"
    r"a\s+\d+(?:[.,]\d+)?\s*(?:minutes?|km)\s+d(?:e|u|es)|"
    r"(?:notre\s+)?agence(?:\s+immobiliere)?\s+d(?:e|u|es)|"
    r"vue(?:\s+degagee)?\s+(?:sur|vers))"
    r"\s+(?:(?:la|le|les|l)\s+)?$"
)
_NAMED_PLACE_PREFIX = re.compile(
    r"(?:rue|avenue|boulevard|chemin|impasse|allee|route|residence|"
    r"ecole|college|lycee|eglise|hopital|clinique)"
    r"(?:\s+(?:de|du|des|la|le|les|l))?\s+$"
)


def _normalise(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _is_proximity_mention(text: str, match_start: int) -> bool:
    return bool(_PROXIMITY_PREFIX.search(text[max(0, match_start - 35) : match_start]))


def _is_named_place_mention(field: str, text: str, match_start: int) -> bool:
    if field not in {"address", "title", "description"}:
        return False
    prefix = text[max(0, match_start - 45) : match_start]
    return bool(_NAMED_PLACE_PREFIX.search(prefix))


def _find(text: str, phrase: str) -> int | None:
    match = re.search(rf"\b{re.escape(phrase)}\b", text)
    return match.start() if match else None


def manifest_outside_scope(listing: Mapping[str, Any]) -> OutsideScopeEvidence | None:
    """Return evidence only for an explicit, reviewed outside-scope locality."""

    for field in _FIELDS:
        text = _normalise(listing.get(field))
        if not text:
            continue

        river = _find(text, "la riviere")
        saint_louis = _find(text, "saint louis")
        if river is not None and saint_louis is not None:
            first = min(river, saint_louis)
            if not _is_proximity_mention(text, first):
                return OutsideScopeEvidence("La Riviere / Saint-Louis", field, "la riviere saint louis")

        for locality, phrase in _OUTSIDE_LOCALITIES:
            start = _find(text, phrase)
            if (
                start is not None
                and not _is_proximity_mention(text, start)
                and not _is_named_place_mention(field, text, start)
            ):
                return OutsideScopeEvidence(locality, field, phrase)

    return None
