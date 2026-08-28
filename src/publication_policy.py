"""One non-destructive eligibility policy shared by every public consumer."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Mapping
from src.geo_scope_guard import manifest_outside_scope


MIN_SURFACE_M2 = 65.0
MAX_RENT_EUR = 1700.0

@dataclass(frozen=True)
class PublicationDecision:
    eligible: bool
    reason: str | None = None

def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None

def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(str(value).replace("\u202f", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None

def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()

def _is_manifestly_commercial(row: Mapping[str, Any]) -> bool:
    commercial_types = {"commercial", "commerce", "local commercial", "bureau", "bureaux", "retail"}
    for key in ("type", "property_type", "property_type_normalized"):
        value = _norm(row.get(key))
        if value in commercial_types:
            return True
    text = " ".join(_norm(row.get(key)) for key in ("title", "description"))
    # Narrow, affirmative inventory signals.  Incidental phrases such as
    # "espace bureau" and "proche du centre commercial" remain residential.
    return bool(re.search(
        r"\b(?:bail commercial|cession (?:de |du |d un )?bail|droit au bail|"
        r"fonds? de commerce|locaux? commerciaux?)\b",
        text,
    ))

def _excluded_district(city: Any, district: Any) -> str | None:
    if _norm(city) not in {"saint denis", "st denis"}:
        return None
    value = _norm(district)
    if re.search(r"\b(?:la )?providence\b", value):
        return "saint_denis_providence"
    if re.search(r"\b(?:saint|st) francois\b", value):
        return "saint_denis_saint_francois"
    return None

def _has_affirmative_location(pattern: str, text: str) -> bool:
    proximity = re.compile(r"(?:(?:proche|a proximite|a cote) d(?:e|u|es)|a \d+ (?:minutes?|km) d(?:e|u|es))(?: quartier)?\s*$")
    for match in re.finditer(pattern, text):
        if not proximity.search(text[max(0, match.start() - 40):match.start()]):
            return True
    return False


def _excluded_district_from_text(city: Any, row: Mapping[str, Any]) -> str | None:
    """Detect only affirmative district locations, never proximity or street mentions."""
    if _norm(city) not in {"saint denis", "st denis"}:
        return None
    title = _norm(row.get("title"))
    description = _norm(row.get("description"))
    text = f"{title} {description}".strip()

    saint_francois_location = _has_affirmative_location(
        r"\b(?:quartier(?: de)?|(?:secteur )?(?:bas|hauts?) de) (?:saint|st) francois\b",
        text,
    )
    # Common source wording places the property directly at/in the district.
    # Keep proximity mentions exempt through the shared helper.
    saint_francois_location = saint_francois_location or _has_affirmative_location(
        r"\b(?:a|au|dans) (?:saint|st) francois\b",
        text,
    )
    saint_francois_title = _has_affirmative_location(r"\b(?:saint|st) francois$", title)
    if saint_francois_location or saint_francois_title:
        return "saint_denis_saint_francois"

    if _has_affirmative_location(r"\b(?:quartier de la|secteur(?: de la)?) providence\b", text):
        return "saint_denis_providence"
    return None




def evaluate_publication(row: Mapping[str, Any]) -> PublicationDecision:
    surface = _number(_first(row, "surface", "surface_m2"))
    if surface is None or surface <= 0:
        return PublicationDecision(False, "surface_missing_or_invalid")
    if surface < MIN_SURFACE_M2:
        return PublicationDecision(False, "surface_below_65")
    rent = _number(_first(row, "rent", "rent_eur", "price"))
    if rent is None or rent <= 0:
        return PublicationDecision(False, "rent_missing_or_invalid")
    if rent > MAX_RENT_EUR:
        return PublicationDecision(False, "rent_above_1700")
    residential = _first(row, "residential", "is_residential")
    if residential is False or residential == 0 or _norm(residential) in {
        "false", "no", "non",
    }:
        return PublicationDecision(False, "non_residential")
    if _is_manifestly_commercial(row):
        return PublicationDecision(False, "non_residential_commercial")
    city = _first(row, "commune", "city")
    city_norm = _norm(city)
    if not city_norm:
        return PublicationDecision(False, "commune_missing_or_invalid")
    if city_norm not in {"saint denis", "st denis", "sainte marie", "ste marie"}:
        return PublicationDecision(False, "commune_outside_scope")
    outside = manifest_outside_scope(row)
    if outside is not None:
        return PublicationDecision(False, "manifest_outside_scope")
    district = _first(row, "quartier", "district", "primary_zone", "location_label")
    reason = _excluded_district(city, district) or _excluded_district_from_text(city, row)
    if reason is None and row.get("commune") not in (None, ""):
        # Some portals put the Saint-Denis district in city while preserving
        # the actual commune separately; true city values do not match here.
        reason = _excluded_district(row.get("commune"), row.get("city"))

    return PublicationDecision(not bool(reason), reason)
