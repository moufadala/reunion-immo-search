"""Conservative public-feed deduplication: ambiguity always stays visible."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _num(item: dict[str, Any], key: str) -> float | None:
    try:
        return float(item.get(key)) if item.get(key) is not None else None
    except (TypeError, ValueError):
        return None


def _near(a: float | None, b: float | None, tolerance: float) -> bool:
    return a is not None and b is not None and abs(a - b) <= tolerance


def _hard_contradiction(a: dict[str, Any], b: dict[str, Any]) -> bool:
    # A different floor/address can identify distinct apartments in the same
    # residence. Bedroom counts, however, are frequently inferred differently
    # by syndicators, so they are only used for the weaker photo rule below.
    for key in ("rooms", "floor", "furnished"):
        if a.get(key) not in (None, "") and b.get(key) not in (None, "") and _norm(a.get(key)) != _norm(b.get(key)):
            return True
    address_a, address_b = _norm(a.get("address")), _norm(b.get("address"))
    return bool(address_a and address_b and address_a != address_b)


def _same_mirrored_content(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Recognise cross-portal syndication without relying on transformed photos.

    Exact public characteristics alone are deliberately insufficient: two
    apartments in one residence can share them. A long, near-identical body and
    exact title provide the independent evidence that the old address/photo
    rule lacked for Zimo mirrors.
    """
    if _norm(a.get("source")) == _norm(b.get("source")):
        return False
    if not _norm(a.get("title")) or _norm(a.get("title")) != _norm(b.get("title")):
        return False
    description_a, description_b = _norm(a.get("description")), _norm(b.get("description"))
    if min(len(description_a), len(description_b)) < 120:
        return False
    return SequenceMatcher(None, description_a, description_b, autojunk=False).ratio() >= 0.88


def _duplicate_reason(a: dict[str, Any], b: dict[str, Any]) -> str | None:
    if _norm(a.get("url")) and _norm(a.get("url")) == _norm(b.get("url")):
        return "same_url"
    if _hard_contradiction(a, b):
        return None
    if _norm(a.get("commune")) != _norm(b.get("commune")) or _norm(a.get("type")) != _norm(b.get("type")):
        return None
    if not _near(_num(a, "rent"), _num(b, "rent"), max(10, 0.03 * min(_num(a, "rent") or 0, _num(b, "rent") or 0))):
        return None
    if not _near(_num(a, "surface"), _num(b, "surface"), 3):
        return None
    address_a, address_b = _norm(a.get("address")), _norm(b.get("address"))
    common_images = set(a.get("images") or []) & set(b.get("images") or [])
    bedrooms_conflict = (a.get("bedrooms") not in (None, "") and b.get("bedrooms") not in (None, "")
                         and _norm(a.get("bedrooms")) != _norm(b.get("bedrooms")))
    if not bedrooms_conflict and address_a and address_a == address_b and common_images:
        return "same_address_and_photo"
    if _same_mirrored_content(a, b):
        return "same_title_and_description"
    return None


def _quality(item: dict[str, Any]) -> tuple[int, int, int]:
    return (int(item.get("active") is True), len(str(item.get("description") or "")), len(item.get("images") or []))


def _merge_complementary(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    for key, value in secondary.items():
        if merged.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
            merged[key] = value
    merged["images"] = list(dict.fromkeys(
        list(primary.get("images") or []) + list(secondary.get("images") or [])
    ))
    return merged


def _link(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item.get("id"), "source": item.get("source"), "url": item.get("url")}


def deduplicate_public_feed(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    visible: list[dict[str, Any]] = []
    hidden = groups = 0
    for original in items:
        item = dict(original)
        found = next(((existing, _duplicate_reason(existing, item)) for existing in visible if _duplicate_reason(existing, item)), None)
        if found is None:
            visible.append(item)
            continue
        match, reason = found
        links = list(match.get("also_on") or [_link(match)]) + [_link(item)]
        group_id = "dedup:" + hashlib.sha256("|".join(sorted(str(x.get("id")) for x in links)).encode()).hexdigest()[:16]
        if not match.get("dedup_group_id"):
            groups += 1
        canonical = item if _quality(item) > _quality(match) else match
        other = match if canonical is item else item
        canonical = _merge_complementary(canonical, other)
        canonical_id = canonical.get("id")
        canonical.update({
            "also_on": links,
            "seen_also_on": sorted({str(link.get("source")) for link in links if link.get("source")}),
            "dedup_group_id": group_id,
            "canonical_id": canonical_id,
            "canonical_display_id": canonical_id,
            "display_canonical": True,
            "dedup_decision": "canonical",
            "dedup_reason": reason,
            "dedup_confidence": "high",
        })
        visible[visible.index(match)] = canonical
        hidden += 1
    return visible, {"input": len(items), "visible": len(visible), "groups": groups, "hidden_duplicates": hidden}
