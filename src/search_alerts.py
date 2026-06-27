#!/usr/bin/env python3
"""Non-destructive saved-search alert engine for the Reunion immo dashboard.

Default behavior is cron-safe:
- Reads generated listings JSON and configured searches.
- Maintains a seen-state file per search.
- If the state file does not exist, bootstraps it silently so the first cron run does not spam.
- Prints a Telegram-ready digest only when new matching listing IDs appear.

Use --dry-run/--preview to inspect matches without writing state.
Use --bootstrap-silent to explicitly create/refresh the baseline without emitting.
Use --live for a capped one-shot digest suitable for an external notifier.
Use --notify-initial to emit current matches on the first run.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_CONFIG = ROOT / "config" / "saved_searches.json"

from src.reunion_geo_search_contract import BUSINESS_LOCATIONS, COMMUNE_ALIASES, NEGATIVE_LOCATION_CONTEXT, norm as geo_norm

ARRAY_URL_KEYS = {
    "region": "r",
    "zones": "z",
    "commune": "c",
    "property_type": "t",
    "furnished": "f",
    "source_site": "src",
}
NUM_URL_KEYS = ["rentMin", "rentMax", "surfaceMin", "roomsMin", "bedroomsMin", "minScore"]
SEARCH_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """Return operator-facing config errors before any state mutation."""
    errors: list[str] = []
    searches = cfg.get("searches")
    if not isinstance(searches, list):
        return ["config.searches must be a list"]
    seen: set[str] = set()
    for i, search in enumerate(searches):
        if not isinstance(search, dict):
            errors.append(f"search[{i}] must be an object")
            continue
        sid = str(search.get("id") or "")
        if not SEARCH_ID_RE.match(sid):
            errors.append(f"search[{i}].id invalid: {sid!r} (use lowercase slug, 2-64 chars)")
        elif sid in seen:
            errors.append(f"duplicate search id: {sid}")
        seen.add(sid)
        filters = search.get("filters") or {}
        if not isinstance(filters, dict):
            errors.append(f"search[{sid or i}].filters must be an object")
            continue
        for key in ("rentMin", "rentMax", "surfaceMin", "roomsMin", "bedroomsMin", "minScore"):
            val = filters.get(key)
            if val is not None and not isinstance(val, (int, float)):
                errors.append(f"search[{sid or i}].filters.{key} must be numeric")
    try:
        max_items = int(cfg.get("max_items_per_search") or 6)
        if max_items < 1 or max_items > 20:
            errors.append("max_items_per_search must be between 1 and 20")
    except Exception:
        errors.append("max_items_per_search must be numeric")
    return errors


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
    loc = item.get("location_intelligence") or {}
    inferred = loc.get("commune_inferred") if isinstance(loc, dict) else None
    if inferred and inferred != "Non précisée":
        return inferred
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


def explicit_location_text(item: dict[str, Any]) -> str:
    """Source-facing location evidence, excluding derived intelligence fields."""
    return norm(" ".join(str(item.get(k) or "") for k in ["title", "description", "url"]))


COMMUNE_NEGATIVE_LOCATION_CONTEXT: dict[str, list[str]] = {
    "Sainte-Marie": ["saint paul", "st paul", "saint-gilles", "saint gilles", "st gilles", "saint-pierre", "saint pierre", "le tampon"],
    "Ste Marie": ["saint paul", "st paul", "saint-gilles", "saint gilles", "st gilles", "saint-pierre", "saint pierre", "le tampon"],
}


def zone_aliases(zone: str) -> list[str]:
    """Expand a saved-search zone through the shared Réunion geo/search contract."""
    zn = geo_norm(zone)
    out: list[str] = [zone]
    for commune, aliases in COMMUNE_ALIASES.items():
        candidates = [commune, *aliases]
        if zn in {geo_norm(x) for x in candidates}:
            out.extend(candidates)
    for label, spec in BUSINESS_LOCATIONS.items():
        candidates = [label, *spec["aliases"]]
        if zn in {geo_norm(x) for x in candidates}:
            out.extend(candidates)
    return list(dict.fromkeys(x for x in out if x))


def negative_context_for_zone(zone: str) -> list[str]:
    """Return source-facing contexts that veto stale/derived zone matches."""
    zn = geo_norm(zone)
    out: list[str] = []
    for commune, aliases in COMMUNE_ALIASES.items():
        candidates = [commune, *aliases]
        if zn in {geo_norm(x) for x in candidates}:
            out.extend(COMMUNE_NEGATIVE_LOCATION_CONTEXT.get(commune, []))
            out.extend(COMMUNE_NEGATIVE_LOCATION_CONTEXT.get(str(zone), []))
    out.extend(COMMUNE_NEGATIVE_LOCATION_CONTEXT.get(str(zone), []))
    for label, spec in BUSINESS_LOCATIONS.items():
        candidates = [label, *spec["aliases"]]
        if zn in {geo_norm(x) for x in candidates}:
            out.extend(NEGATIVE_LOCATION_CONTEXT.get(label, []))
    return list(dict.fromkeys(out))


def has_conflicting_location_evidence(item: dict[str, Any], zones: list[str]) -> bool:
    text = " " + explicit_location_text(item).replace("-", " ") + " "
    wanted = [norm(a).replace("-", " ") for z in zones for a in zone_aliases(str(z))]
    # If source text itself contains a requested zone, keep it even if an agency
    # URL mentions another town. Otherwise veto obvious conflicting communes.
    if any(re.search(rf"\b{re.escape(w)}\b", text) for w in wanted if w):
        return False
    conflicts: list[str] = []
    for z in zones:
        conflicts.extend(negative_context_for_zone(str(z)))
    return any(re.search(rf"\b{re.escape(norm(c).replace('-', ' '))}\b", text) for c in conflicts if c)


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
        if has_conflicting_location_evidence(item, zones):
            return False
        item_zones = list(item.get("zones") or [])
        for k in ("primary_zone", "district", "location", "city", "commune", "location_label", "title", "description"):
            if item.get(k):
                item_zones.append(item[k])
        # Zone matching needs to handle real Réunion phrasing: accents,
        # hyphens, and quartier names often appear in title/description rather
        # than a normalized zones[] field. Keep exact matching, but add a
        # conservative normalized contains check so "Rivière des Pluies" or
        # "Beauséjour" can match text exports without broadening to all Nord.
        wanted = [norm(a).replace("-", " ") for z in zones for a in zone_aliases(str(z))]
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
    loc = item.get("location_label") or item.get("location") or item.get("district") or item_commune(item) or item.get("city") or item.get("region") or "secteur n.c."
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
    loc = item.get("location_label") or item.get("location") or item.get("district") or item_commune(item) or item.get("city") or item.get("region") or "secteur n.c."
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


def dedupe_new_by_listing(new_by_search: list[tuple[dict[str, Any], list[dict[str, Any]], int]]) -> list[tuple[dict[str, Any], list[dict[str, Any]], int]]:
    """Avoid digest spam when one listing matches several saved searches."""
    seen: set[str] = set()
    out: list[tuple[dict[str, Any], list[dict[str, Any]], int]] = []
    for search, items, total_matches in new_by_search:
        kept: list[dict[str, Any]] = []
        for item in items:
            iid = str(item.get("id") or "")
            if iid and iid in seen:
                continue
            if iid:
                seen.add(iid)
            kept.append(item)
        if kept:
            out.append((search, kept, total_matches))
    return out


def dedupe_events_by_id(event_by_search: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    seen: set[str] = set()
    out: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for search, events in event_by_search:
        kept: list[dict[str, Any]] = []
        for ev in events:
            key = str(ev.get("event_id") or f"{ev.get('listing_id')}:{ev.get('event_type')}:{ev.get('event_at')}")
            if key in seen:
                continue
            seen.add(key)
            kept.append(ev)
        if kept:
            out.append((search, kept))
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


def output_summary(
    *,
    dry_run: bool,
    source: Path,
    history_db: Path,
    state_path: Path,
    state_exists: bool,
    bootstrap_silent: bool,
    summaries: list[dict[str, Any]],
    event_by_search: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    digest: str,
    fmt: str,
) -> None:
    new_total = sum(int(s.get("new") or 0) for s in summaries)
    event_total = sum(len(evs) for _, evs in event_by_search)
    payload = {
        "ok": True,
        "dry_run": dry_run,
        "source": str(source),
        "history_db": str(history_db),
        "state_path": str(state_path),
        "state_exists": state_exists,
        "bootstrap_silent": bootstrap_silent,
        "would_emit": bool(digest),
        "new_total": new_total,
        "event_total": event_total,
        "searches": summaries,
        "event_searches": [{"id": s.get("id"), "events": len(evs)} for s, evs in event_by_search],
        "message": digest,
    }
    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print("Alertes sauvegardées — preview")
    print(f"- Source: {source}")
    print(f"- État: {state_path} ({'existe' if state_exists else 'absent'})")
    print(f"- Bootstrap silencieux: {'oui' if bootstrap_silent else 'non'}")
    print(f"- Émission potentielle: {'oui' if digest else 'non'} ({new_total} nouvelle(s), {event_total} événement(s))")
    for s in summaries:
        print(f"- {s['name']} [{s['id']}]: {s['matches']} match(s), {s['new']} nouvelle(s)")
        print(f"  URL: {s['search_url']}")
        top_ids = s.get("top_ids") or []
        if top_ids:
            print(f"  Top IDs: {', '.join(map(str, top_ids))}")
    if digest:
        print("\n--- Digest qui serait produit ---")
        print(digest, end="")


def status_report(*, cfg: dict[str, Any], source: Path, state_path: Path, history_db: Path, searches: list[dict[str, Any]], fmt: str) -> None:
    payload: dict[str, Any] = {
        "ok": True,
        "source": str(source),
        "source_exists": source.exists(),
        "state_path": str(state_path),
        "state_exists": state_path.exists(),
        "history_db": str(history_db),
        "history_db_exists": history_db.exists(),
        "enabled_searches": len(searches),
        "configured_searches": len(cfg.get("searches", []) or []),
        "searches": [],
    }
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            state = load_json(state_path)
            payload["updated_at"] = state.get("updated_at")
        except Exception as exc:
            payload["state_error"] = str(exc)
    state_searches = state.get("searches", {}) if isinstance(state, dict) else {}
    for s in searches:
        sid = s["id"]
        st = state_searches.get(sid) or {}
        payload["searches"].append({
            "id": sid,
            "name": s.get("name") or sid,
            "enabled": s.get("enabled", True),
            "last_checked_at": st.get("last_checked_at"),
            "last_match_count": st.get("last_match_count"),
            "seen_count": len(st.get("seen_ids") or []),
            "search_url": st.get("search_url"),
        })
    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print("Alertes sauvegardées — état")
    print(f"- Source: {payload['source']} ({'ok' if payload['source_exists'] else 'absente'})")
    print(f"- État: {payload['state_path']} ({'ok' if payload['state_exists'] else 'absent'})")
    print(f"- Historique: {payload['history_db']} ({'ok' if payload['history_db_exists'] else 'absent'})")
    print(f"- Recherches actives/configurées: {payload['enabled_searches']}/{payload['configured_searches']}")
    if payload.get("updated_at"):
        print(f"- Dernière mise à jour état: {payload['updated_at']}")
    if payload.get("state_error"):
        print(f"- ERREUR état: {payload['state_error']}")
    for s in payload["searches"]:
        print(f"- {s['name']} [{s['id']}]: checked={s['last_checked_at'] or 'jamais'}, seen={s['seen_count']}, last_match_count={s['last_match_count']}")


def write_state_with_backup(state_path: Path, state: dict[str, Any], *, backup: bool = True) -> Path | None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    if backup and state_path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = state_path.with_name(f"{state_path.name}.bak-{stamp}")
        shutil.copy2(state_path, backup_path)
    tmp = state_path.with_name(f".{state_path.name}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_path)
    return backup_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--dry-run", action="store_true", help="Do not write seen state; print JSON summary.")
    ap.add_argument("--preview", action="store_true", help="Human-readable dry preview; never writes state.")
    ap.add_argument("--status", action="store_true", help="Print configured search/state status; never writes state.")
    ap.add_argument("--bootstrap-silent", action="store_true", help="Explicitly create/refresh baseline without emitting a digest.")
    ap.add_argument("--live", action="store_true", help="Controlled one-shot live run: requires existing state and caps emissions.")
    ap.add_argument("--max-emit-total", type=int, default=10, help="Maximum new+event entries allowed in --live before refusing to write/emit.")
    ap.add_argument("--format", choices=["json", "text"], default=None, help="Output format for preview/status/bootstrap summaries.")
    ap.add_argument("--notify-initial", action="store_true", help="On first run, emit all current matches instead of bootstrapping silently.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if sum(bool(x) for x in (args.preview, args.status, args.bootstrap_silent, args.live)) > 1:
        ap.error("--preview, --status, --bootstrap-silent and --live are mutually exclusive")
    if args.bootstrap_silent and args.notify_initial:
        ap.error("--bootstrap-silent cannot be combined with --notify-initial")

    cfg_path = Path(args.config)
    cfg = load_json(cfg_path)
    config_errors = validate_config(cfg if isinstance(cfg, dict) else {})
    if config_errors:
        print(json.dumps({"ok": False, "error": "invalid_saved_search_config", "errors": config_errors}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 64
    source = Path(cfg.get("source_json") or ROOT / "artifacts" / "app" / "listings.json")
    state_path = Path(cfg.get("state_path") or "/opt/data/artifacts/immo-alerts/seen.json")
    history_db = Path(cfg.get("history_db") or "/opt/data/artifacts/immo-alerts/history.sqlite")
    searches = [s for s in cfg.get("searches", []) if s.get("enabled", True)]
    max_items = int(cfg.get("max_items_per_search") or 6)
    base_url = cfg.get("public_base_url") or "https://immo.148.230.103.174.sslip.io/"

    fmt = args.format or ("json" if args.dry_run else "text")
    if args.status:
        status_report(cfg=cfg, source=source, state_path=state_path, history_db=history_db, searches=searches, fmt=fmt)
        return 0

    payload = load_json(source)
    listings = payload.get("listings") or []

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
        if args.bootstrap_silent or (not state_exists and not args.notify_initial):
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

    raw_new_total = sum(len(items) for _, items, _ in new_by_search)
    new_by_search = dedupe_new_by_listing(new_by_search)
    event_by_search = [] if args.bootstrap_silent else load_history_events(history_db, since_by_search, searches, max_items)
    raw_event_total = sum(len(evs) for _, evs in event_by_search)
    event_by_search = dedupe_events_by_id(event_by_search)
    deduped_new = raw_new_total - sum(len(items) for _, items, _ in new_by_search)
    deduped_events = raw_event_total - sum(len(evs) for _, evs in event_by_search)
    for s in summaries:
        s["dedupe_policy"] = "first_matching_search_wins"
    if deduped_new or deduped_events:
        summaries.append({
            "id": "_anti_spam",
            "name": "Anti-spam dédoublonnage",
            "matches": 0,
            "new": 0,
            "search_url": "",
            "top_ids": [],
            "deduped_new": deduped_new,
            "deduped_events": deduped_events,
        })

    state["updated_at"] = now
    digest = build_digest(new_by_search, event_by_search, base_url, max_items) if (new_by_search or event_by_search) else ""
    if args.dry_run or args.preview:
        output_summary(
            dry_run=True,
            source=source,
            history_db=history_db,
            state_path=state_path,
            state_exists=state_exists,
            bootstrap_silent=(not state_exists and not args.notify_initial) or args.bootstrap_silent,
            summaries=summaries,
            event_by_search=event_by_search,
            digest=digest,
            fmt=fmt,
        )
        return 0

    if args.live and not state_exists:
        print(json.dumps({
            "ok": False,
            "error": "live_requires_existing_state",
            "hint": "Run --bootstrap-silent first, then retry --live.",
            "state_path": str(state_path),
        }, ensure_ascii=False), file=sys.stderr)
        return 2
    emit_total = sum(len(items) for _, items, _ in new_by_search) + sum(len(evs) for _, evs in event_by_search)
    if args.live and emit_total > args.max_emit_total:
        print(json.dumps({
            "ok": False,
            "error": "live_emit_cap_exceeded",
            "emit_total": emit_total,
            "max_emit_total": args.max_emit_total,
            "state_written": False,
        }, ensure_ascii=False), file=sys.stderr)
        return 2

    backup_path = write_state_with_backup(state_path, state, backup=state_exists)
    if args.bootstrap_silent:
        payload = {
            "ok": True,
            "bootstrap_silent": True,
            "state_path": str(state_path),
            "backup_path": str(backup_path) if backup_path else None,
            "searches": summaries,
        }
        if fmt == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.verbose:
            print(f"Bootstrap silencieux OK: {state_path}" + (f" (backup: {backup_path})" if backup_path else ""))
        return 0

    if digest:
        print(digest, end="")
    elif args.verbose:
        print(json.dumps({"ok": True, "new": 0, "backup_path": str(backup_path) if backup_path else None, "searches": summaries}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
