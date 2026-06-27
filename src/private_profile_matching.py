#!/usr/bin/env python3
"""Dry-run matcher for private Réunion immo watch profiles.

This module is deliberately non-transport: it never sends Telegram. It converts
private profile criteria into explainable strict/fallback decisions over a
listings JSON export so operators can validate before any live alert path.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "private_profiles.example.json"
DEFAULT_LISTINGS = ROOT / "artifacts" / "app" / "listings.json"


def norm(text: Any) -> str:
    s = str(text or "").lower().replace("-", " ").replace("_", " ").replace("'", " ")
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def price(item: dict[str, Any]) -> int | None:
    v = item.get("rent_eur", item.get("price"))
    try:
        return int(round(float(v))) if v not in (None, "") else None
    except Exception:
        return None


def surface(item: dict[str, Any]) -> float | None:
    v = item.get("surface_m2", item.get("surface"))
    try:
        return float(v) if v not in (None, "") else None
    except Exception:
        return None


def bedrooms(item: dict[str, Any]) -> int | None:
    v = item.get("bedrooms")
    try:
        return int(v) if v not in (None, "") else None
    except Exception:
        return None


def item_type(item: dict[str, Any]) -> str:
    return str(item.get("property_type") or item.get("type") or "")


def item_furnished(item: dict[str, Any]) -> str:
    return str(item.get("furnished") or "Non précisé")


def searchable_location(item: dict[str, Any]) -> str:
    loc = item.get("location_intelligence") or {}
    parts: list[Any] = [
        item.get("title"),
        item.get("description"),
        item.get("url"),
        item.get("region"),
        item.get("commune"),
        item.get("city"),
        item.get("primary_zone"),
        item.get("district"),
        item.get("location"),
        item.get("location_label"),
        " ".join(str(x) for x in item.get("zones") or []),
    ]
    if isinstance(loc, dict):
        parts.extend([loc.get("commune_inferred"), loc.get("district_best"), loc.get("precise_location_label")])
    return norm(" ".join(str(p or "") for p in parts))


def has_zone(item: dict[str, Any], zones: list[str]) -> tuple[bool, str | None]:
    blob = " " + searchable_location(item) + " "
    for zone in zones:
        z = norm(zone)
        if z and re.search(rf"\b{re.escape(z)}\b", blob):
            return True, zone
    return False, None


def has_garden(item: dict[str, Any]) -> bool | None:
    blob = norm(" ".join(str(item.get(k) or "") for k in ["title", "description", "decision_summary"]))
    if re.search(r"\b(jardin|terrain|cour|exterieur|extérieur)\b", blob):
        return True
    return None


def floor_warning(item: dict[str, Any], profile: dict[str, Any]) -> str | None:
    """Conservative warning extraction for the mother profile.

    Unknown floor stays a warning, not a rejection. If floor evidence says above
    first and no elevator is mentioned, warn strongly.
    """
    criteria = profile.get("criteria") or {}
    if criteria.get("elevator_required_above_floor") is None:
        return None
    blob = norm(" ".join(str(item.get(k) or "") for k in ["title", "description", "decision_summary"]))
    if re.search(r"\b(rdc|rez de chaussee|rez de chaussée|1er|premier)\b", blob):
        return None
    if re.search(r"\b(2e|2eme|deuxieme|3e|3eme|troisieme|4e|4eme|etage|étage)\b", blob):
        if re.search(r"\b(ascenseur|elevator)\b", blob):
            return "floor_above_first_with_elevator"
        return "floor_without_elevator"
    return "missing_floor_or_elevator_data"


def search_url(base_url: str, profile: dict[str, Any]) -> str:
    criteria = profile.get("criteria") or {}
    params: dict[str, str] = {"immo": "1"}
    if criteria.get("zones"):
        params["z"] = "|".join(criteria["zones"])
    if criteria.get("property_type"):
        params["t"] = "|".join(criteria["property_type"])
    for src, dest in [("rent_min_eur", "rentMin"), ("rent_max_eur", "rentMax"), ("surface_min_m2", "surfaceMin"), ("bedrooms_min", "bedroomsMin")]:
        if criteria.get(src) is not None:
            params[dest] = str(criteria[src])
    return base_url.rstrip("/") + "/?" + urlencode(params)



def within_ratio(value: float | int | None, target: float | int, tolerance: float) -> bool:
    if value is None:
        return False
    return abs(float(value) - float(target)) / max(float(target), 1.0) <= tolerance


def evaluate_near_miss(profile: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    """Return a controlled near-miss candidate.

    Near-miss is deliberately conservative: the listing must match property type
    and one target zone, then miss only by bounded numeric/preferences gaps. This
    avoids broad noisy alerts when strict/fallback returns zero.
    """
    if item.get("db_is_canonical") is False:
        return None
    criteria = profile.get("criteria") or {}
    allowed_types = criteria.get("property_type") or []
    if allowed_types and item_type(item) not in allowed_types:
        return None
    ok_zone, matched_zone = has_zone(item, list(criteria.get("zones") or []))
    if not ok_zone:
        return None

    reasons = [f"type {item_type(item) or 'n.c.'}", f"zone {matched_zone}"]
    miss_reasons: list[str] = []
    warnings: list[str] = []

    p = price(item)
    rent_min = int(criteria.get("rent_min_eur", 0))
    rent_max = int(criteria.get("rent_max_eur", 10**9))
    if p is None:
        miss_reasons.append("rent_unknown")
    elif p < rent_min:
        if within_ratio(p, rent_min, 0.12):
            miss_reasons.append(f"rent_below_target:{p}<{rent_min}")
        else:
            return None
    elif p > rent_max:
        if within_ratio(p, rent_max, 0.12):
            miss_reasons.append(f"rent_above_target:{p}>{rent_max}")
        else:
            return None
    else:
        reasons.append(f"loyer {p}€")

    surf = surface(item)
    surf_min = float(criteria.get("surface_min_m2", 0))
    if surf is None:
        miss_reasons.append("surface_unknown")
    elif surf < surf_min:
        if within_ratio(surf, surf_min, 0.10):
            miss_reasons.append(f"surface_below_target:{surf:g}<{surf_min:g}")
        else:
            return None
    else:
        reasons.append(f"surface {surf:g}m²")

    b = bedrooms(item)
    bedrooms_min = criteria.get("bedrooms_min")
    if bedrooms_min is not None:
        if b is None:
            miss_reasons.append("bedrooms_unknown")
        elif b < int(bedrooms_min):
            fb_min = (profile.get("fallbacks") or {}).get("bedrooms_min")
            if fb_min is not None and b >= int(fb_min):
                warnings.append("two_bedrooms_fallback")
            elif b + 1 >= int(bedrooms_min):
                miss_reasons.append(f"bedrooms_below_target:{b}<{bedrooms_min}")
            else:
                return None
        else:
            reasons.append(f"{b} chambre(s)")

    furnished = item_furnished(item)
    preferred = criteria.get("furnished_preference")
    if preferred and furnished != preferred:
        allowed_fallback = set((profile.get("fallbacks") or {}).get("furnished") or [])
        if furnished in allowed_fallback:
            warnings.append("furnished_fallback")
        else:
            return None
    elif preferred:
        reasons.append(preferred)

    if "garden_preferred" in (profile.get("preferences") or []):
        garden = has_garden(item)
        if garden:
            reasons.append("jardin mentionné")
        else:
            warnings.append("garden_missing_or_unknown")

    if not miss_reasons:
        return None
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "decision": "near_miss",
        "reasons": reasons,
        "warnings": sorted(set(warnings)),
        "miss_reasons": miss_reasons,
        "rent_eur": p,
        "surface_m2": surf,
        "bedrooms": b,
        "location_label": item.get("location_label") or item.get("primary_zone") or item.get("commune"),
        "url": item.get("url"),
    }

def evaluate_item(profile: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("db_is_canonical") is False:
        return None
    criteria = profile.get("criteria") or {}
    fallbacks = profile.get("fallbacks") or {}
    reasons: list[str] = []
    warnings: list[str] = []

    allowed_types = criteria.get("property_type") or []
    if allowed_types and item_type(item) not in allowed_types:
        return None
    reasons.append(f"type {item_type(item) or 'n.c.'}")

    ok_zone, matched_zone = has_zone(item, list(criteria.get("zones") or []))
    if not ok_zone:
        return None
    reasons.append(f"zone {matched_zone}")

    p = price(item)
    if p is None or p < int(criteria.get("rent_min_eur", 0)) or p > int(criteria.get("rent_max_eur", 10**9)):
        return None
    reasons.append(f"loyer {p}€")

    s = surface(item)
    if s is None or s < float(criteria.get("surface_min_m2", 0)):
        return None
    reasons.append(f"surface {s:g}m²")

    b = bedrooms(item)
    bedrooms_min = criteria.get("bedrooms_min")
    if bedrooms_min is not None:
        if b is None:
            return None
        if b < int(bedrooms_min):
            fb_min = fallbacks.get("bedrooms_min")
            if fb_min is not None and b >= int(fb_min):
                warnings.append("two_bedrooms_fallback")
            else:
                return None
        else:
            reasons.append(f"{b} chambre(s)")

    furnished = item_furnished(item)
    preferred_furnished = criteria.get("furnished_preference")
    if preferred_furnished and furnished != preferred_furnished:
        allowed_fallback = set(fallbacks.get("furnished") or [])
        if furnished in allowed_fallback:
            warnings.append("furnished_fallback")
        else:
            return None
    else:
        reasons.append(str(preferred_furnished or furnished))

    fw = floor_warning(item, profile)
    if fw:
        if fw == "floor_without_elevator":
            warnings.append(fw)
        elif fw in profile.get("warnings", []) or fw in fallbacks:
            warnings.append(fw)

    if "garden_preferred" in (profile.get("preferences") or []):
        garden = has_garden(item)
        if garden:
            reasons.append("jardin mentionné")
        elif fallbacks.get("missing_garden"):
            warnings.append("garden_missing_or_unknown")

    decision = "fallback_match" if warnings else "strict_match"
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "decision": decision,
        "reasons": reasons,
        "warnings": sorted(set(warnings)),
        "rent_eur": p,
        "surface_m2": s,
        "bedrooms": b,
        "location_label": item.get("location_label") or item.get("primary_zone") or item.get("commune"),
        "url": item.get("url"),
    }


def build_payload(config: dict[str, Any], listings_data: dict[str, Any], include_near_miss: bool = False, digest: bool = False) -> dict[str, Any]:
    base_url = config.get("public_base_url") or "/"
    listings = listings_data.get("listings") or []
    profiles_out: list[dict[str, Any]] = []
    for profile in config.get("profiles") or []:
        matches = []
        near_misses = []
        matched_ids: set[Any] = set()
        for item in listings:
            out = evaluate_item(profile, item)
            if out:
                matches.append(out)
                matched_ids.add(out.get("id"))
        if include_near_miss:
            for item in listings:
                if item.get("id") in matched_ids:
                    continue
                near = evaluate_near_miss(profile, item)
                if near:
                    near_misses.append(near)
        decision_rank = {"strict_match": 0, "fallback_match": 1, "needs_review": 2}
        matches.sort(key=lambda x: (decision_rank.get(x.get("decision"), 9), x.get("rent_eur") or 10**9))
        near_misses.sort(key=lambda x: (len(x.get("miss_reasons") or []), x.get("rent_eur") or 10**9))
        profiles_out.append({
            "profile_id": profile.get("id"),
            "name": profile.get("name"),
            "scope": profile.get("scope"),
            "alert_mode": profile.get("alert_mode"),
            "search_url": search_url(base_url, profile),
            "match_count": len(matches),
            "strict_count": sum(1 for x in matches if x.get("decision") == "strict_match"),
            "fallback_count": sum(1 for x in matches if x.get("decision") == "fallback_match"),
            "matches": matches[:20],
            "near_miss_count": len(near_misses),
            "near_misses": near_misses[:10],
        })
    payload = {
        "ok": True,
        "dry_run": True,
        "scope": config.get("scope"),
        "mode": config.get("mode"),
        "telegram_live_enabled": bool(config.get("telegram_live_enabled")),
        "listing_count": len(listings),
        "profiles": profiles_out,
        "notice": "Dry-run only. No Telegram transport is called by this script.",
    }
    if digest:
        payload["digest_text"] = render_digest(payload)
    return payload



def render_digest(payload: dict[str, Any]) -> str:
    lines = ["🏠 Immo privé — DRY-RUN (AUCUN ENVOI TELEGRAM LIVE)", f"Annonces analysées: {payload.get('listing_count')}", ""]
    for profile in payload.get("profiles", []):
        lines.append(f"## {profile.get('name') or profile.get('profile_id')}")
        lines.append(f"Matchs: {profile.get('match_count')} strict/fallback · Near-miss: {profile.get('near_miss_count', 0)}")
        items = list(profile.get("matches") or [])[:3]
        if not items:
            items = list(profile.get("near_misses") or [])[:3]
        if not items:
            lines.append("- Aucun strict/fallback/near_miss utile dans ce run.")
        for item in items:
            lines.append(f"- {item.get('decision')} · {item.get('rent_eur') or 'n.c.'}€ · {item.get('surface_m2') or 'n.c.'}m² · {item.get('title')}")
            if item.get("reasons"):
                lines.append("  Raisons: " + "; ".join(item.get("reasons") or []))
            if item.get("warnings"):
                lines.append("  Warnings: " + "; ".join(item.get("warnings") or []))
            if item.get("miss_reasons"):
                lines.append("  Écarts: " + "; ".join(item.get("miss_reasons") or []))
            if item.get("url"):
                lines.append("  Source: " + str(item.get("url")))
        lines.append("")
    lines.append("Sécurité: dry-run seulement; aucune fonction d'envoi n'est appelée.")
    return "\n".join(lines)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--listings", type=Path, default=DEFAULT_LISTINGS)
    ap.add_argument("--dry-run", action="store_true", help="Required safety flag; script never sends Telegram.")
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--include-near-miss", action="store_true", help="Include controlled near-miss candidates when exact matches are scarce.")
    ap.add_argument("--digest", action="store_true", help="Embed a human-readable dry-run digest in JSON output.")
    args = ap.parse_args()

    cfg = load_json(args.config)
    if cfg.get("telegram_live_enabled") is not False or cfg.get("mode") != "dry_run_only":
        print("ERROR: private profile config must stay dry_run_only with telegram_live_enabled=false", file=sys.stderr)
        return 2
    if not args.dry_run:
        print("ERROR: --dry-run is required for private profiles at this stage", file=sys.stderr)
        return 2
    listings_data = load_json(args.listings)
    payload = build_payload(cfg, listings_data, include_near_miss=args.include_near_miss, digest=args.digest)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "dry_run": True, "json_out": str(args.json_out), "profiles": len(payload["profiles"])}, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
