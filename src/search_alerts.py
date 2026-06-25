#!/usr/bin/env python3
"""Non-destructive saved-search alert engine for the Reunion immo dashboard.

Default behavior is cron-safe:
- Reads generated listings JSON and configured searches.
- Maintains a seen-state file per search.
- If the state file does not exist, bootstraps it silently so the first cron run does not spam.
- Prints a Telegram-ready digest only when new matching listing IDs appear.

Use --dry-run to inspect matches without writing state.
Use --notify-initial to emit current matches on the first run.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "saved_searches.json"

ARRAY_URL_KEYS = {
    "region": "r",
    "zones": "z",
    "commune": "c",
    "property_type": "t",
    "furnished": "f",
    "source_site": "src",
}
NUM_URL_KEYS = ["rentMin", "rentMax", "surfaceMin", "roomsMin", "bedroomsMin", "minScore"]


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def norm(text: Any) -> str:
    import unicodedata

    s = str(text or "").lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


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


def item_type(item: dict[str, Any]) -> str | None:
    return item.get("property_type") or item.get("type")


def item_score(item: dict[str, Any]) -> int | float | None:
    """Alert score: prefer opportunity score over generic listing quality score."""
    opp = item.get("opportunity_analysis") or {}
    v = opp.get("score") if isinstance(opp, dict) else None
    if v is None:
        v = item.get("score")
    try:
        return float(v) if v not in (None, "") else None
    except Exception:
        return None


def item_source(item: dict[str, Any]) -> str | None:
    return item.get("source_site") or item.get("source")


def item_commune(item: dict[str, Any]) -> str | None:
    return item.get("commune") or item.get("city")


def searchable(item: dict[str, Any]) -> str:
    parts = [
        item.get("title"),
        item.get("city"),
        item.get("region"),
        item.get("commune"),
        item.get("primary_zone"),
        item.get("district"),
        item.get("location"),
        item.get("location_label"),
        item.get("description"),
        item.get("decision_summary"),
        item_source(item),
        " ".join(item.get("residential_reasons") or []),
        " ".join(item.get("trust_flags") or []),
    ]
    return norm(" ".join(str(p or "") for p in parts))


def list_intersects(values: list[str] | None, item_values: list[str]) -> bool:
    if not values:
        return True
    return any(v in item_values for v in values)


def matches(item: dict[str, Any], filters: dict[str, Any]) -> bool:
    if item.get("db_is_canonical") is False:
        return False
    q = filters.get("q")
    if q and norm(q) not in searchable(item):
        return False
    for key in ["region", "furnished"]:
        vals = filters.get(key) or []
        if vals and item.get(key) not in vals:
            return False
    vals = filters.get("commune") or []
    if vals and item_commune(item) not in vals:
        return False
    vals = filters.get("property_type") or []
    if vals and item_type(item) not in vals:
        return False
    vals = filters.get("source_site") or []
    if vals and item_source(item) not in vals:
        return False
    zones = filters.get("zones") or []
    if zones:
        item_zones = list(item.get("zones") or [])
        for k in ("primary_zone", "district", "location", "city", "commune", "location_label", "title", "description"):
            if item.get(k):
                item_zones.append(item[k])
        # Zone matching needs to handle real Réunion phrasing: accents,
        # hyphens, and quartier names often appear in title/description rather
        # than a normalized zones[] field. Keep exact matching, but add a
        # conservative normalized contains check so "Rivière des Pluies" or
        # "Beauséjour" can match text exports without broadening to all Nord.
        wanted = [norm(z).replace("-", " ") for z in zones]
        haystacks = [norm(z).replace("-", " ") for z in item_zones]
        if not any(w == h or w in h for w in wanted for h in haystacks):
            return False
    p = price(item)
    s = surface(item)
    if filters.get("rentMin") is not None and (p is None or p < filters["rentMin"]):
        return False
    if filters.get("rentMax") is not None and (p is None or p > filters["rentMax"]):
        return False
    if filters.get("surfaceMin") is not None and (s is None or s < filters["surfaceMin"]):
        return False
    if filters.get("roomsMin") is not None and (not item.get("rooms") or item["rooms"] < filters["roomsMin"]):
        return False
    if filters.get("bedroomsMin") is not None and (not item.get("bedrooms") or item["bedrooms"] < filters["bedroomsMin"]):
        return False
    return True


def sort_items(items: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "priceAsc":
        return sorted(items, key=lambda x: (price(x) or 10**9, -(item_score(x) or 0)))
    if sort == "surfaceDesc":
        return sorted(items, key=lambda x: (-(surface(x) or 0), -(item_score(x) or 0)))
    if sort == "recent":
        return sorted(items, key=lambda x: str(x.get("seen_last_at") or ""), reverse=True)
    return sorted(items, key=lambda x: (-(item_score(x) or 0), price(x) or 10**9))


def search_url(base_url: str, filters: dict[str, Any]) -> str:
    params: dict[str, str] = {"immo": "1"}
    for key, url_key in ARRAY_URL_KEYS.items():
        vals = filters.get(key) or []
        if vals:
            params[url_key] = "|".join(vals)
    for key in NUM_URL_KEYS:
        if filters.get(key) is not None:
            params[key] = str(filters[key])
    if filters.get("q"):
        params["q"] = str(filters["q"])
    if filters.get("tab") and filters.get("tab") != "all":
        params["tab"] = str(filters["tab"])
    if filters.get("sort") and filters.get("sort") != "score":
        params["sort"] = str(filters["sort"])
    sep = "&" if "?" in base_url else "?"
    return base_url.rstrip("/") + "/" + sep + urlencode(params)


def item_line(item: dict[str, Any]) -> str:
    p = price(item)
    s = surface(item)
    price_txt = f"{p}€" if p else "prix n.c."
    surface_txt = f"{round(s)}m²" if s else "surface n.c."
    rooms = f"{item.get('rooms')}p" if item.get("rooms") else "?p"
    loc = item.get("location_label") or item.get("commune") or item.get("city") or item.get("region") or "secteur n.c."
    title = (item.get("title") or "Annonce").strip()
    if len(title) > 92:
        title = title[:89] + "…"
    score = item_score(item)
    score_txt = f" · score opportunité {round(score)}" if isinstance(score, (int, float)) else ""
    why = item.get("decision_summary") or "à vérifier"
    tags = " · ".join((item.get("variant_tags") or [])[:3])
    reason = f"\n  Pourquoi: {why}{(' · '+tags) if tags else ''}"
    return f"- {price_txt} · {surface_txt} · {rooms} · {loc}{score_txt} — {title}{reason}\n  {item.get('url') or ''}"


def event_line(ev: dict[str, Any]) -> str:
    item = ev.get("item") or {}
    title = (item.get("title") or ev.get("title") or "Annonce").strip()
    if len(title) > 86:
        title = title[:83] + "…"
    loc = item.get("location_label") or item.get("commune") or item.get("city") or item.get("region") or "secteur n.c."
    url = item.get("url") or ev.get("url") or ""
    typ = ev.get("event_type")
    if typ == "price_changed":
        old = ev.get("old_value")
        new = ev.get("new_value")
        delta = ev.get("delta_eur")
        badge = "📉 Baisse" if isinstance(delta, int) and delta < 0 else "💶 Prix modifié"
        delta_txt = f" ({delta}€)" if isinstance(delta, int) else ""
        return f"- {badge}: {old}€ → {new}€{delta_txt} · {loc} — {title}\n  {url}"
    if typ == "disappeared":
        price_txt = f"{price(item)}€" if price(item) else "prix n.c."
        return f"- 👻 Disparue: {price_txt} · {loc} — {title}\n  {url}"
    if typ == "reappeared":
        price_txt = f"{price(item)}€" if price(item) else "prix n.c."
        return f"- 🔁 Réapparue: {price_txt} · {loc} — {title}\n  {url}"
    return f"- {typ}: {loc} — {title}\n  {url}"


def load_history_events(db_path: Path, since_by_search: dict[str, str | None], searches: list[dict[str, Any]], max_events_per_search: int) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Return relevant price/status events since each search's previous check.

    First-run-safe: a search without previous last_checked_at gets no historical
    events, preventing the initial baseline from spamming Telegram.
    """
    if not db_path.exists() or not any(since_by_search.values()):
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT e.event_id,e.listing_id,e.event_type,e.event_at,e.old_value,e.new_value,e.details_json,c.raw_json
            FROM listing_events e
            LEFT JOIN listing_current c ON c.id=e.listing_id
            WHERE e.event_type IN ('price_changed','disappeared','reappeared')
            ORDER BY e.event_at DESC, e.event_id DESC
            """
        ).fetchall()
    finally:
        con.close()

    out: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for search in searches:
        sid = search["id"]
        since = since_by_search.get(sid)
        if not since:
            continue
        filters = search.get("filters") or {}
        evs: list[dict[str, Any]] = []
        seen_event_ids: set[int] = set()
        for row in rows:
            if str(row["event_at"]) <= str(since):
                continue
            try:
                item = json.loads(row["raw_json"] or "{}")
            except Exception:
                item = {}
            try:
                details = json.loads(row["details_json"] or "{}") if row["details_json"] else {}
            except Exception:
                details = {}
            old_i = new_i = delta = None
            if row["event_type"] == "price_changed":
                old_i = int(row["old_value"]) if row["old_value"] not in (None, "") else None
                new_i = int(row["new_value"]) if row["new_value"] not in (None, "") else None
                # Current product need: alert only useful drops; increases create noise.
                if old_i is None or new_i is None or new_i >= old_i:
                    continue
                delta = new_i - old_i
            if not item or not matches(item, filters):
                continue
            event_id = int(row["event_id"])
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            evs.append({
                "event_id": event_id,
                "listing_id": row["listing_id"],
                "event_type": row["event_type"],
                "event_at": row["event_at"],
                "old_value": old_i if old_i is not None else row["old_value"],
                "new_value": new_i if new_i is not None else row["new_value"],
                "delta_eur": delta,
                "item": item,
                "title": details.get("title"),
                "url": details.get("url"),
            })
        if evs:
            out.append((search, evs[:max_events_per_search]))
    return out


def effective_url_filters(search: dict[str, Any]) -> dict[str, Any]:
    filters = dict(search.get("filters") or {})
    min_score = search.get("min_score")
    if min_score is not None and filters.get("minScore") is None:
        filters["minScore"] = min_score
    return filters


def build_digest(new_by_search: list[tuple[dict[str, Any], list[dict[str, Any]], int]], event_by_search: list[tuple[dict[str, Any], list[dict[str, Any]]]], base_url: str, max_items: int) -> str:
    total_new = sum(len(items) for _, items, _ in new_by_search)
    total_events = sum(len(items) for _, items in event_by_search)
    parts = []
    if total_new:
        parts.append(f"{total_new} nouvelle(s)")
    if total_events:
        parts.append(f"{total_events} événement(s) prix/statut")
    lines = ["🏠 Immo Réunion — " + " · ".join(parts)]
    for search, items, total_matches in new_by_search:
        filters = effective_url_filters(search)
        lines.append("")
        lines.append(f"## {search.get('name') or search.get('id')}")
        lines.append(f"{len(items)} nouvelle(s) · {total_matches} match(s) actuels")
        lines.append(search_url(base_url, filters))
        for item in items[:max_items]:
            lines.append(item_line(item))
        if len(items) > max_items:
            lines.append(f"… +{len(items)-max_items} autres dans la recherche")
    for search, events in event_by_search:
        filters = effective_url_filters(search)
        lines.append("")
        lines.append(f"## {search.get('name') or search.get('id')} — changements")
        lines.append(search_url(base_url, filters))
        for ev in events[:max_items]:
            lines.append(event_line(ev))
        if len(events) > max_items:
            lines.append(f"… +{len(events)-max_items} autres changements")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--dry-run", action="store_true", help="Do not write seen state; print JSON summary.")
    ap.add_argument("--notify-initial", action="store_true", help="On first run, emit all current matches instead of bootstrapping silently.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = load_json(cfg_path)
    source = Path(cfg.get("source_json") or ROOT / "artifacts" / "app" / "listings.json")
    state_path = Path(cfg.get("state_path") or "/opt/data/artifacts/immo-alerts/seen.json")
    history_db = Path(cfg.get("history_db") or "/opt/data/artifacts/immo-alerts/history.sqlite")
    payload = load_json(source)
    listings = payload.get("listings") or []
    searches = [s for s in cfg.get("searches", []) if s.get("enabled", True)]
    max_items = int(cfg.get("max_items_per_search") or 6)
    base_url = cfg.get("public_base_url") or "https://immo.148.230.103.174.sslip.io/"

    state_exists = state_path.exists()
    state = load_json(state_path) if state_exists else {"version": 1, "searches": {}}
    state.setdefault("searches", {})

    summaries: list[dict[str, Any]] = []
    new_by_search: list[tuple[dict[str, Any], list[dict[str, Any]], int]] = []
    since_by_search = {s["id"]: (state["searches"].get(s["id"]) or {}).get("last_checked_at") for s in searches}
    now = datetime.now(timezone.utc).isoformat()

    for search in searches:
        sid = search["id"]
        filters = search.get("filters") or {}
        matched = sort_items([x for x in listings if matches(x, filters)], filters.get("sort") or "score")
        min_score = search.get("min_score", cfg.get("min_score"))
        if min_score is not None:
            matched = [x for x in matched if (item_score(x) or 0) >= int(min_score)]
        matched_ids = [x["id"] for x in matched]
        prev = set((state["searches"].get(sid) or {}).get("seen_ids") or [])
        if not state_exists and not args.notify_initial:
            new_items: list[dict[str, Any]] = []
        else:
            new_items = [x for x in matched if x["id"] not in prev]
        state["searches"][sid] = {
            "name": search.get("name") or sid,
            "last_checked_at": now,
            "last_match_count": len(matched),
            "seen_ids": matched_ids,
            "search_url": search_url(base_url, effective_url_filters(search)),
        }
        summaries.append({
            "id": sid,
            "name": search.get("name") or sid,
            "matches": len(matched),
            "new": len(new_items),
            "search_url": state["searches"][sid]["search_url"],
            "top_ids": matched_ids[:5],
        })
        if new_items:
            new_by_search.append((search, new_items, len(matched)))

    event_by_search = load_history_events(history_db, since_by_search, searches, max_items)

    state["updated_at"] = now
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "source": str(source), "history_db": str(history_db), "state_exists": state_exists, "searches": summaries, "event_searches": [{"id": s.get("id"), "events": len(evs)} for s, evs in event_by_search]}, ensure_ascii=False, indent=2))
        return 0

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if new_by_search or event_by_search:
        print(build_digest(new_by_search, event_by_search, base_url, max_items), end="")
    elif args.verbose:
        print(json.dumps({"ok": True, "new": 0, "searches": summaries}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
