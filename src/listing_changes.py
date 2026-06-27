#!/usr/bin/env python3
"""Export a small public/admin-friendly changes journal from listing history.

Non-destructive: reads history.sqlite, writes artifacts/app/changes.json and
optionally artifacts/app/changes.html by default.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path("/opt/data/artifacts/immo-alerts/history.sqlite")
DEFAULT_OUT = ROOT / "artifacts" / "app" / "changes.json"
DEFAULT_HTML_OUT = ROOT / "artifacts" / "app" / "changes.html"
PUBLIC_BASE_URL = "https://immo.148.230.103.174.sslip.io/"
EVENT_TYPES = ("price_changed", "disappeared", "reappeared")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def as_int(v: Any) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(round(float(v)))
    except Exception:
        return None


def portable_image_url(url: str | None) -> str | None:
    if not url:
        return None
    u = str(url).strip()
    if u.startswith('/thumbs/') or u.startswith('thumbs/'):
        rel = u.lstrip('/')
        # History can reference thumbnails that were not copied into the current
        # public artifact. Do not render broken images in changes.html: a missing
        # thumbnail should degrade to the explicit “Pas de photo” placeholder and
        # not produce browser console 404s.
        if not (DEFAULT_HTML_OUT.parent / rel).exists():
            return None
        return rel
    return u


def clean_public_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"https?://\S+", "", str(value))
    text = re.sub(r"\b(?:serp_view|distributionTypes|estateTypes|locations|search|page)=[^\s]+", "", text, flags=re.I)
    text = re.sub(r"[#?&][A-Za-z0-9_%=+&.,:-]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -·;,.\n\t")
    return text or None


def item_public(raw: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback or {}
    item_id = raw.get("id") or fallback.get("listing_id")
    source_site = raw.get("source_site") or raw.get("source")
    if not source_site and item_id and ":" in str(item_id):
        source_site = str(item_id).split(":", 1)[0]
    return {
        "id": item_id,
        "title": raw.get("title") or fallback.get("title") or "Annonce",
        "url": raw.get("url") or fallback.get("url"),
        "source_site": source_site,
        "region": raw.get("region"),
        "commune": raw.get("commune"),
        "primary_zone": raw.get("primary_zone"),
        "zones": raw.get("zones") or ([raw.get("primary_zone")] if raw.get("primary_zone") else []),
        "property_type": raw.get("property_type"),
        "furnished": raw.get("furnished"),
        "location_label": raw.get("location_label") or raw.get("commune") or raw.get("region"),
        "rent_eur": as_int(raw.get("rent_eur")),
        "surface_m2": raw.get("surface_m2"),
        "rooms": raw.get("rooms"),
        "bedrooms": raw.get("bedrooms"),
        "image_url": portable_image_url(raw.get("local_image_url") or raw.get("image_url")),
        "description": clean_public_text(raw.get("description")),
        "decision_summary": clean_public_text(raw.get("decision_summary")),
        "seen_last_at": raw.get("seen_last_at") or raw.get("last_seen_at"),
    }


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def days_between(start: Any, end: Any) -> float | None:
    a = parse_dt(start)
    b = parse_dt(end)
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 86400, 2)


def compact_event(row: sqlite3.Row) -> dict[str, Any]:
    old_value = as_int(row["old_value"])
    new_value = as_int(row["new_value"])
    return {
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "event_at": row["event_at"],
        "old_value": old_value if old_value is not None else row["old_value"],
        "new_value": new_value if new_value is not None else row["new_value"],
    }


def lifecycle_for(row: sqlite3.Row, listing_history: list[sqlite3.Row]) -> dict[str, Any]:
    event_at = row["event_at"]
    before = [e for e in listing_history if str(e["event_at"]) <= str(event_at) and int(e["event_id"]) != int(row["event_id"])]
    after = [e for e in listing_history if str(e["event_at"]) > str(event_at)]
    last_disappeared = next((e for e in reversed(before) if e["event_type"] == "disappeared"), None)
    last_reappeared = next((e for e in reversed(before) if e["event_type"] == "reappeared"), None)
    nearby_price = None
    row_dt = parse_dt(event_at)
    if row_dt:
        close: list[tuple[float, sqlite3.Row]] = []
        for e in listing_history:
            if e["event_type"] != "price_changed" or int(e["event_id"]) == int(row["event_id"]):
                continue
            dt = parse_dt(e["event_at"])
            if not dt:
                continue
            hours = abs((dt - row_dt).total_seconds()) / 3600
            if hours <= 72:
                close.append((hours, e))
        if close:
            nearby_price = min(close, key=lambda x: x[0])[1]
    status_events = [e for e in listing_history if e["event_type"] in ("disappeared", "reappeared")]
    price_events = [e for e in listing_history if e["event_type"] == "price_changed"]
    lifecycle: dict[str, Any] = {
        "status_event_count": len(status_events),
        "price_event_count": len(price_events),
        "recent_status_events": [compact_event(e) for e in status_events[-6:]],
        "recent_price_events": [compact_event(e) for e in price_events[-5:]],
    }
    if row["event_type"] == "reappeared":
        lifecycle.update({
            "last_disappeared_at": last_disappeared["event_at"] if last_disappeared else None,
            "offline_days": days_between(last_disappeared["event_at"], event_at) if last_disappeared else None,
            "cycle_label": "réapparition après disparition" if last_disappeared else "réapparition sans disparition antérieure connue",
        })
    if row["event_type"] == "disappeared":
        next_reappeared = next((e for e in after if e["event_type"] == "reappeared"), None)
        lifecycle.update({
            "last_reappeared_at": last_reappeared["event_at"] if last_reappeared else None,
            "next_reappeared_at": next_reappeared["event_at"] if next_reappeared else None,
            "offline_days_until_next_reappearance": days_between(event_at, next_reappeared["event_at"]) if next_reappeared else None,
        })
    if nearby_price:
        old_p, new_p = as_int(nearby_price["old_value"]), as_int(nearby_price["new_value"])
        lifecycle["nearby_price_change"] = compact_event(nearby_price)
        if old_p is not None and new_p is not None:
            lifecycle["nearby_price_change"]["delta_eur"] = new_p - old_p
            lifecycle["nearby_price_change"]["direction"] = "down" if new_p < old_p else "up" if new_p > old_p else "same"
    return lifecycle


def classify(row: sqlite3.Row, item: dict[str, Any], lifecycle: dict[str, Any] | None = None) -> dict[str, Any]:
    old_value = as_int(row["old_value"])
    new_value = as_int(row["new_value"])
    delta = None
    direction = None
    if row["event_type"] == "price_changed" and old_value is not None and new_value is not None:
        delta = new_value - old_value
        direction = "down" if delta < 0 else "up" if delta > 0 else "same"
    severity = "info"
    if row["event_type"] == "price_changed" and direction == "down":
        severity = "positive"
    elif row["event_type"] == "disappeared":
        severity = "watch"
    elif row["event_type"] == "reappeared":
        severity = "info"
    nearby_price = (lifecycle or {}).get("nearby_price_change") if row["event_type"] == "reappeared" else None
    classification_note = None
    if nearby_price:
        d = nearby_price.get("direction")
        classification_note = "Réapparue + baisse de prix: classée comme signal prioritaire après les baisses directes." if d == "down" else "Réapparue + changement de prix: conservée dans Réapparues et signalée comme cas mixte."
    return {
        "event_id": row["event_id"],
        "listing_id": row["listing_id"],
        "event_type": row["event_type"],
        "event_at": row["event_at"],
        "old_value": old_value if old_value is not None else row["old_value"],
        "new_value": new_value if new_value is not None else row["new_value"],
        "delta_eur": delta,
        "direction": direction,
        "severity": severity,
        "lifecycle": lifecycle or {},
        "classification_note": classification_note,
        "item": item,
    }


def export_changes(db_path: Path = DEFAULT_DB, out_path: Path = DEFAULT_OUT, limit: int = 80) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        payload = {
            "meta": {
                "generated_at": utcnow(),
                "db": db_path.name,
                "ok": False,
                "reason": "history_db_missing",
                "public_base_url": PUBLIC_BASE_URL,
            },
            "summary": {"total_events": 0, "by_type": {}, "price_drops": 0},
            "changes": [],
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

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
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        listing_ids = [r["listing_id"] for r in rows]
        history_by_id: dict[str, list[sqlite3.Row]] = {lid: [] for lid in listing_ids}
        if listing_ids:
            placeholders = ",".join("?" for _ in listing_ids)
            history_rows = con.execute(
                f"""
                SELECT event_id,listing_id,event_type,event_at,old_value,new_value,details_json
                FROM listing_events
                WHERE listing_id IN ({placeholders})
                  AND event_type IN ('price_changed','disappeared','reappeared')
                ORDER BY listing_id, event_at, event_id
                """,
                listing_ids,
            ).fetchall()
            for hrow in history_rows:
                history_by_id.setdefault(hrow["listing_id"], []).append(hrow)
        all_counts = dict(con.execute("SELECT event_type, COUNT(*) c FROM listing_events GROUP BY event_type").fetchall())
        active_rows = con.execute("SELECT COUNT(*) c FROM listing_current WHERE active=1").fetchone()["c"]
        current_rows = con.execute("SELECT COUNT(*) c FROM listing_current").fetchone()["c"]
    finally:
        con.close()

    changes: list[dict[str, Any]] = []
    for row in rows:
        details = safe_json_loads(row["details_json"], {})
        raw = safe_json_loads(row["raw_json"], {})
        fallback = {"listing_id": row["listing_id"], "title": details.get("title"), "url": details.get("url")}
        item = item_public(raw, fallback)
        lifecycle = lifecycle_for(row, history_by_id.get(row["listing_id"], []))
        changes.append(classify(row, item, lifecycle))

    by_type = Counter(c["event_type"] for c in changes)
    price_drops = sum(1 for c in changes if c.get("event_type") == "price_changed" and c.get("direction") == "down")
    latest_event_at = changes[0]["event_at"] if changes else None

    def display_priority(change: dict[str, Any]) -> tuple[int, float]:
        # Product contract: price movements are the actionable signal and must not be
        # buried below many disappeared listings. A reappearance with a nearby price
        # change is a mixed case: keep it visible ahead of plain reappearances and
        # explain the classification on the card/modal. Keep recency inside each group.
        event_type = change.get("event_type")
        direction = change.get("direction")
        mixed_reappeared_price = bool((change.get("lifecycle") or {}).get("nearby_price_change"))
        if event_type == "price_changed" and direction == "down":
            priority = 0
        elif event_type == "price_changed":
            priority = 1
        elif event_type == "reappeared" and mixed_reappeared_price:
            priority = 2
        elif event_type == "reappeared":
            priority = 3
        else:
            priority = 4
        dt = parse_dt(change.get("event_at"))
        return (priority, -(dt.timestamp() if dt else 0))

    changes.sort(key=display_priority)
    payload = {
        "meta": {
            "generated_at": utcnow(),
            "db": db_path.name,
            "ok": True,
            "public_base_url": PUBLIC_BASE_URL,
            "current_rows": current_rows,
            "active_rows": active_rows,
            "latest_event_at": latest_event_at,
            "limit": limit,
            "note": "Baseline new events are intentionally excluded; this file exposes only price/status changes.",
        },
        "summary": {
            "total_events": len(changes),
            "by_type": dict(by_type),
            "all_history_counts": {str(k): int(v) for k, v in all_counts.items()},
            "price_drops": price_drops,
        },
        "changes": changes,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def h(value: Any) -> str:
    return html_lib.escape("" if value is None else str(value), quote=True)


def fmt_eur(value: Any) -> str:
    iv = as_int(value)
    return f"{iv:,}".replace(",", " ") + " €" if iv is not None else "prix n.c."


def fmt_surface(value: Any, title: str = "") -> str | None:
    if value not in (None, "", "?"):
        try:
            f = float(str(value).replace(",", "."))
            return (str(int(f)) if f.is_integer() else str(f).rstrip("0").rstrip(".")) + " m²"
        except Exception:
            return f"{value} m²"
    m = re.search(r"(\d+(?:[,.]\d+)?)\s*m[²2]\b", title or "", flags=re.I)
    if m:
        f = float(m.group(1).replace(",", "."))
        return (str(int(f)) if f.is_integer() else str(f).rstrip("0").rstrip(".")) + " m²"
    return None


def event_label(change: dict[str, Any]) -> str:
    et = change.get("event_type")
    if et == "price_changed":
        if change.get("direction") == "down":
            return "Baisse de prix"
        if change.get("direction") == "up":
            return "Hausse de prix"
        return "Prix modifié"
    if et == "disappeared":
        return "Annonce disparue"
    if et == "reappeared":
        return "Annonce réapparue"
    return str(et or "Changement")


def event_sentence(change: dict[str, Any]) -> str:
    et = change.get("event_type")
    lifecycle = change.get("lifecycle") or {}
    if et == "price_changed":
        delta = change.get("delta_eur")
        delta_txt = ""
        if isinstance(delta, int):
            delta_txt = f" ({delta:+,} €)".replace(",", " ")
        return f"{fmt_eur(change.get('old_value'))} → {fmt_eur(change.get('new_value'))}{delta_txt}"
    if et == "disappeared":
        nxt = lifecycle.get("next_reappeared_at")
        if nxt:
            return f"Disparue puis réapparue le {str(nxt)[:10]} — cycle conservé."
        return "La source ne présente plus cette annonce dans le dernier snapshot."
    if et == "reappeared":
        offline = lifecycle.get("offline_days")
        price = lifecycle.get("nearby_price_change") or {}
        bits = ["L’annonce est revenue dans le périmètre public"]
        if offline is not None:
            bits.append(f"après {offline:g} jour(s) hors ligne")
        if price:
            bits.append("avec changement de prix proche")
        return " ".join(bits) + "."
    return "Changement enregistré dans l’historique."


def type_badge(change: dict[str, Any]) -> str:
    et = change.get("event_type")
    direction = change.get("direction")
    if et == "price_changed" and direction == "down":
        return "drop"
    if et == "disappeared":
        return "gone"
    if et == "reappeared":
        return "back"
    return "other"


def render_change_card(change: dict[str, Any]) -> str:
    item = change.get("item") or {}
    source = item.get("source_site") or "source n.c."
    loc = item.get("location_label") or item.get("commune") or item.get("region") or "secteur n.c."
    title = item.get("title") or "Annonce"
    url = item.get("url") or "#"
    image = item.get("image_url")
    when = str(change.get("event_at") or "date n.c.")[:19].replace("T", " ")
    display_price = change.get("new_value") if change.get("event_type") == "price_changed" else item.get("rent_eur")
    surface_txt = fmt_surface(item.get("surface_m2"), title)
    zones = item.get("zones") or ([item.get("primary_zone")] if item.get("primary_zone") else [])
    zone_text = " ".join(str(z) for z in zones if z)
    data_q = " ".join([title, loc, source, str(item.get("region") or ""), str(item.get("commune") or ""), zone_text, str(item.get("description") or "")])
    note = change.get("classification_note")
    cycle = change.get("lifecycle") or {}
    cycle_bits = []
    if cycle.get("offline_days") is not None:
        cycle_bits.append(f"hors ligne {cycle.get('offline_days'):g} j")
    if cycle.get("status_event_count"):
        cycle_bits.append(f"{cycle.get('status_event_count')} statut(s)")
    if cycle.get("price_event_count"):
        cycle_bits.append(f"{cycle.get('price_event_count')} prix")
    img_html = f'<img src="{h(image)}" alt="" loading="lazy">' if image else '<span>Pas de photo</span>'
    return f"""
    <article class="change-card" data-event-id="{h(change.get('event_id'))}" data-type="{h(change.get('event_type'))}" data-direction="{h(change.get('direction'))}" data-region="{h(item.get('region') or '')}" data-commune="{h(item.get('commune') or '')}" data-zone="{h(zone_text or item.get('primary_zone') or '')}" data-q="{h(data_q)}">
      <button class="card-open" type="button" data-event-id="{h(change.get('event_id'))}" aria-label="Consulter {h(title)}">
        <div class="thumb">{img_html}</div>
        <div class="change-body">
          <div class="rowline"><span class="badge {type_badge(change)}">{h(event_label(change))}</span><span class="date">{h(when)}</span></div>
          <h2>{h(title)}</h2>
          <p class="location">{h(loc)} · {h(source)}</p>
          <p class="sentence">{h(event_sentence(change))}</p>
          <div class="meta-line">
            <span>{fmt_eur(display_price)}</span>
            {f'<span>{h(surface_txt)}</span>' if surface_txt else ''}
            <span>{h(item.get('rooms') or '?')} pièces</span>
            {f'<span>{h(item.get("furnished"))}</span>' if item.get('furnished') else ''}
          </div>
          {f'<p class="cycle-line">Cycle: {h(" · ".join(cycle_bits))}</p>' if cycle_bits else ''}
          {f'<p class="rank-note">{h(note)}</p>' if note else ''}
        </div>
      </button>
      <div class="actions"><a href="{h(url)}" target="_blank" rel="noopener">Ouvrir l’annonce source</a></div>
    </article>
    """


def render_changes_html(payload: dict[str, Any], out_path: Path = DEFAULT_HTML_OUT) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = payload.get("summary") or {}
    meta = payload.get("meta") or {}
    changes = payload.get("changes") or []
    cards = "\n".join(render_change_card(c) for c in changes)
    if not cards:
        baseline = (summary.get("all_history_counts") or {}).get("new", 0)
        cards = f"""
        <section class="empty-state" id="emptyState">
          <div class="empty-icon">✓</div>
          <h2>Aucun changement utile détecté pour l’instant</h2>
          <p>La baseline contient {h(baseline)} annonces <code>new</code>, volontairement exclues du journal pour éviter un faux spam. Les futures baisses, disparitions et réapparitions apparaîtront ici.</p>
        </section>
        """
    price_down_count = sum(1 for c in changes if c.get("event_type") == "price_changed" and c.get("direction") == "down")
    price_up_count = sum(1 for c in changes if c.get("event_type") == "price_changed" and c.get("direction") == "up")
    disappeared_count = sum(1 for c in changes if c.get("event_type") == "disappeared")
    reappeared_count = sum(1 for c in changes if c.get("event_type") == "reappeared")


    priority_price_down = [c for c in changes if c.get("event_type") == "price_changed" and c.get("direction") == "down"][:8]
    priority_reappeared = [c for c in changes if c.get("event_type") == "reappeared"][:3]

    def decision_card(c: dict[str, Any], kind: str) -> str:
        item = c.get("item") or {}
        title = item.get("title") or "Annonce"
        loc = item.get("primary_zone") or item.get("commune") or item.get("region") or "Zone non précisée"
        price_line = event_sentence(c)
        return f'''<article class="decision-card decision-{h(kind)}">
          <strong>{h(event_label(c))}</strong>
          <span>{h(title)}</span>
          <em>{h(loc)}</em>
          <small>{h(price_line)}</small>
        </article>'''

    decision_brief = f'''<section class="panel decision-brief" id="decisionBrief" aria-label="Priorités à regarder">
      <h2>Priorité : à regarder d’abord</h2>
      <p>{h(price_down_count)} baisses · {h(reappeared_count)} réapparitions · {h(disappeared_count)} disparues. Les baisses et retours sont mis devant pour décider vite; les disparues restent dans le journal.</p>
      <div class="decision-grid">
        {''.join(decision_card(c, 'price_down') for c in priority_price_down)}
        {''.join(decision_card(c, 'reappeared') for c in priority_reappeared[:1])}
      </div>
    </section>'''

    def facet_options(kind: str) -> str:
        values: Counter[str] = Counter()
        for c in changes:
            item = c.get("item") or {}
            if kind == "zone":
                vals = item.get("zones") or ([item.get("primary_zone")] if item.get("primary_zone") else [])
            else:
                vals = [item.get(kind)]
            for val in vals:
                if val:
                    values[str(val)] += 1
        return "".join(f'<option value="{h(v)}">{h(v)} ({n})</option>' for v, n in sorted(values.items(), key=lambda x: (-x[1], x[0].casefold())))

    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Journal des changements — Immo Réunion</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{{--bg:#ffffff;--paper:#f6f5f4;--ink:rgba(0,0,0,.94);--muted:#615d59;--soft:#a39e98;--line:rgba(0,0,0,.10);--blue:#0075de;--green:#087f5b;--orange:#b25b00;--red:#b42318;--shadow:rgba(0,0,0,.04) 0 4px 18px,rgba(0,0,0,.027) 0 2px 8px,rgba(0,0,0,.02) 0 .8px 3px;}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}} a{{color:inherit}} .shell{{max-width:1180px;margin:0 auto;padding:0 24px}} .top{{border-bottom:1px solid var(--line);background:rgba(255,255,255,.92);backdrop-filter:blur(16px);position:sticky;top:0;z-index:5}} .nav{{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:16px 0}} .brand{{display:flex;align-items:center;gap:12px;font-weight:800;letter-spacing:-.4px}} .mark{{width:38px;height:38px;border-radius:11px;background:#000;color:#fff;display:grid;place-items:center;font-weight:800}} .nav a{{text-decoration:none;font-size:14px;font-weight:700;border:1px solid var(--line);border-radius:999px;padding:9px 13px;background:#fff}} .hero{{padding:54px 0 28px;display:grid;grid-template-columns:1.25fr .75fr;gap:28px;align-items:end}} .eyebrow{{display:inline-flex;border:1px solid var(--line);background:#f2f9ff;color:#097fe8;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:700;letter-spacing:.12px}} h1{{font-size:clamp(38px,7vw,72px);line-height:.96;letter-spacing:-2.1px;margin:16px 0 14px;max-width:840px}} .lead{{font-size:18px;line-height:1.55;color:var(--muted);max-width:720px;margin:0}} .panel{{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:var(--shadow)}} .panel h2{{margin:0 0 8px;font-size:18px;letter-spacing:-.25px}} .panel p{{margin:0;color:var(--muted);font-size:14px;line-height:1.5}} .stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0 24px}} .stat{{border:1px solid var(--line);border-radius:16px;padding:16px;background:#fff;box-shadow:var(--shadow)}} .stat b{{display:block;font-size:30px;letter-spacing:-1px}} .stat span{{color:var(--muted);font-size:13px;font-weight:600}} .toolbar{{display:flex;gap:10px;align-items:center;justify-content:space-between;margin:12px 0 18px;flex-wrap:wrap}} .filters{{display:flex;gap:8px;flex-wrap:wrap}} button,.search{{border:1px solid var(--line);background:#fff;border-radius:999px;min-height:42px;padding:10px 13px;font:inherit;font-weight:700}} button{{cursor:pointer}} button.active{{background:#000;color:#fff;border-color:#000}} .search{{min-width:min(360px,100%);font-weight:500;outline:none}} .section-title{{display:flex;justify-content:space-between;align-items:end;gap:16px;margin:28px 0 12px}} .section-title h2{{font-size:30px;letter-spacing:-.8px;margin:0}} .section-title p{{color:var(--muted);margin:6px 0 0}} .list{{display:grid;gap:12px;padding-bottom:70px}} .change-card{{display:grid;grid-template-columns:156px 1fr;gap:18px;border:1px solid var(--line);border-radius:18px;background:#fff;padding:12px;box-shadow:var(--shadow)}} .thumb{{background:var(--paper);border-radius:13px;min-height:118px;display:grid;place-items:center;overflow:hidden;color:var(--soft);font-weight:700;font-size:12px}} .thumb img{{width:100%;height:100%;object-fit:cover;display:block}} .rowline,.meta-line,.actions{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}} .rowline{{justify-content:space-between}} .badge{{display:inline-flex;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:800;background:#f2f9ff;color:#097fe8}} .badge.drop{{background:#effaf5;color:var(--green)}} .badge.gone{{background:#fff4e6;color:var(--orange)}} .badge.back{{background:#f2f9ff;color:#097fe8}} .date{{font-size:12px;color:var(--soft);font-weight:700}} .change-body h2{{font-size:22px;line-height:1.18;letter-spacing:-.3px;margin:10px 0 6px}} .location{{margin:0;color:var(--muted);font-weight:600}} .sentence{{font-size:16px;margin:12px 0;color:var(--ink);font-weight:700}} .meta-line span{{border:1px solid var(--line);background:var(--paper);border-radius:999px;padding:5px 8px;font-size:12px;font-weight:700;color:#31302e}} .actions{{margin-top:14px}} .actions a{{text-decoration:none;background:#000;color:#fff;border-radius:999px;padding:9px 12px;font-size:13px;font-weight:800}} .empty-state{{border:1px solid var(--line);border-radius:22px;background:var(--paper);padding:44px;text-align:center;box-shadow:var(--shadow)}} .empty-icon{{width:48px;height:48px;border-radius:50%;display:grid;place-items:center;background:#effaf5;color:var(--green);font-weight:900;margin:0 auto 16px}} .empty-state h2{{font-size:28px;letter-spacing:-.7px;margin:0 0 10px}} .empty-state p{{max-width:680px;margin:auto;color:var(--muted);line-height:1.6}} code{{background:rgba(0,0,0,.06);border-radius:6px;padding:2px 5px}} .foot{{border-top:1px solid var(--line);padding:24px 0 40px;color:var(--muted);font-size:13px}}
.decision-brief{{margin:0 0 18px;background:#fff}}.decision-brief h2{{margin:0 0 6px;font-size:20px;letter-spacing:-.35px}}.decision-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin-top:12px}}.decision-card{{border:1px solid var(--line);border-radius:14px;padding:12px;background:var(--paper);display:grid;gap:5px}}.decision-card strong{{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--green)}}.decision-reappeared strong{{color:var(--blue)}}.decision-card span{{font-weight:800;line-height:1.2}}.decision-card em,.decision-card small{{font-style:normal;color:var(--muted);font-size:12px;line-height:1.3}}.facetbar{{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:10px;width:100%;margin-top:10px}}.facetbar label{{font-size:12px;font-weight:800;color:var(--muted);display:grid;gap:5px}}.facetbar select{{width:100%;border:1px solid var(--line);border-radius:14px;background:white;padding:10px 12px;font-weight:700}}.card-open{{display:grid;grid-template-columns:168px 1fr;gap:14px;width:100%;border:0;background:transparent;text-align:left;color:inherit;padding:0;cursor:pointer}}.change-card{{display:block}}.cycle-line,.rank-note{{font-size:12px;margin:7px 0 0;color:var(--muted);font-weight:700}}.rank-note{{color:var(--orange)}}.actions{{padding:0 0 12px 182px}}.modal{{position:fixed;inset:0;background:rgba(0,0,0,.42);display:none;z-index:30;padding:24px;align-items:center;justify-content:center}}.modal.open{{display:flex}}.modalbox{{background:#fff;border-radius:24px;box-shadow:0 22px 70px rgba(0,0,0,.25);max-width:920px;width:min(920px,100%);max-height:90vh;overflow:auto;display:grid;grid-template-columns:310px 1fr;gap:18px;padding:18px;position:relative}}.modalimg{{min-height:280px;background:var(--paper);border-radius:18px;overflow:hidden;display:grid;place-items:center}}.modalimg img{{width:100%;height:100%;object-fit:cover}}.modalinfo h3{{font-size:26px;margin:8px 0;letter-spacing:-.5px}}.modalinfo p{{color:var(--muted);line-height:1.45}}.modalclose{{position:absolute;right:14px;top:14px;border:1px solid var(--line);background:#fff;border-radius:999px;width:38px;height:38px;font-size:24px;cursor:pointer}}.modalchips{{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0}}.modalchips span{{border:1px solid var(--line);border-radius:999px;padding:6px 9px;font-size:12px;font-weight:800;background:var(--paper)}}@media(max-width:760px){{.shell{{padding:0 10px}}.nav{{align-items:flex-start;padding:8px 0}}.brand{{gap:8px;font-size:14px}}.mark{{width:32px;height:32px;border-radius:9px}}.nav div:last-child{{display:flex;gap:6px;overflow:auto;flex-wrap:nowrap;justify-content:flex-start;padding-bottom:2px}}.nav a{{white-space:nowrap;min-height:40px;padding:8px 11px}}.hero{{display:block;padding:18px 0 10px}}.eyebrow{{display:none}}h1{{font-size:28px;letter-spacing:-.8px;margin:0 0 8px}}.lead{{font-size:13px;line-height:1.35}}.panel{{margin-top:10px;padding:12px;border-radius:14px}}.stats{{display:flex;gap:7px;overflow:auto;margin:8px 0 10px}}.stat{{min-width:116px;padding:8px 10px;border-radius:12px}}.stat b{{font-size:18px}}.stat span{{font-size:10.5px;line-height:1.15}}.toolbar{{display:block;position:sticky;top:49px;z-index:4;background:rgba(255,255,255,.94);border:1px solid var(--line);border-radius:16px;padding:8px;overflow:hidden;margin:8px 0 10px}}.filters{{margin-top:8px;display:flex;gap:6px;overflow:auto;flex-wrap:nowrap;padding-bottom:3px}}.filters button{{white-space:nowrap;min-height:40px;padding:8px 10px;font-size:12px}}.facetbar{{grid-template-columns:1fr;gap:7px}}.facetbar label,.facetbar select{{min-width:0}}.facetbar select,.search{{min-height:44px}}.search{{width:100%;min-width:0}}.section-title{{display:block;margin:12px 0 8px}}.section-title h2{{font-size:21px}}.section-title p{{font-size:12px}}.card-open{{grid-template-columns:92px 1fr;gap:9px}}.actions{{padding:7px 0 8px 101px;margin-top:0}}.actions a{{display:inline-flex;align-items:center;min-height:40px;padding:8px 10px}}.change-card{{padding:8px;border-radius:14px}}.thumb{{min-height:104px;border-radius:11px}}.rowline{{gap:5px}}.badge{{font-size:10.5px;padding:4px 7px}}.change-body h2{{font-size:14px;margin:5px 0 3px;line-height:1.18;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}.sentence{{font-size:12px;margin:6px 0;line-height:1.3}}.location,.date,.meta-line span,.actions a,.cycle-line,.rank-note{{font-size:10.5px}}.meta-line{{gap:4px}}.meta-line span{{padding:4px 6px}}.modal{{align-items:flex-end;padding:0}}.modalbox{{grid-template-columns:1fr;width:100%;max-height:100dvh;border-radius:18px 18px 0 0;padding:12px}}.modalimg{{min-height:190px}}.modalclose{{width:44px;height:44px}}}}
</style>
</head>
<body>
<header class="top"><div class="shell nav"><div class="brand"><div class="mark">974</div><span>Journal changements immo</span></div><div><a href="/">Dashboard</a> <a href="/changes.json" target="_blank" rel="noopener">JSON</a></div></div></header>
<main class="shell">
  <section class="hero">
    <div><span class="eyebrow">Historique public · anti-spam</span><h1>Baisses, disparitions et retours d’annonces.</h1><p class="lead">Ce journal lit l’historique SQLite mais n’affiche pas la baseline <code>new</code>. Objectif : voir les vrais mouvements du marché sans bruit.</p></div>
    <aside class="panel"><h2>État du pipeline</h2><p>Généré le {h(str(meta.get('generated_at') or '')[:19].replace('T',' '))}. Base active : {h(meta.get('active_rows'))} annonces. Dernier événement utile : {h(meta.get('latest_event_at') or 'aucun')}.</p></aside>
  </section>
  <section class="stats" aria-label="Résumé">
    <div class="stat"><b>{h(summary.get('total_events', 0))}</b><span>changements utiles</span></div>
    <div class="stat"><b>{h(summary.get('price_drops', 0))}</b><span>baisses de prix</span></div>
    <div class="stat"><b>{h((summary.get('all_history_counts') or {}).get('new', 0))}</b><span>baseline new exclue</span></div>
    <div class="stat"><b>{h(meta.get('active_rows', 0))}</b><span>actives suivies</span></div>
  </section>
  {decision_brief}
  <section class="toolbar" aria-label="Filtres">
    <input class="search" id="q" placeholder="Filtrer: Moufia, OFIM, Saint-Denis…" autocomplete="off">
    <div class="filters"><button data-filter="all" class="active">Tout ({h(len(changes))})</button><button data-filter="price_down">Baisses ({h(price_down_count)})</button><button data-filter="price_up">Hausses ({h(price_up_count)})</button><button data-filter="disappeared">Disparues ({h(disappeared_count)})</button><button data-filter="reappeared">Réapparues ({h(reappeared_count)})</button></div>
    <div class="facetbar" aria-label="Filtres géographiques">
      <label>Zone / quartier<select id="zoneFilter"><option value="">Toutes zones</option>{facet_options('zone')}</select></label>
      <label>Commune<select id="communeFilter"><option value="">Toutes communes</option>{facet_options('commune')}</select></label>
      <label>Région<select id="regionFilter"><option value="">Toutes régions</option>{facet_options('region')}</select></label>
    </div>
  </section>
  <section class="section-title"><div><h2>Changements récents</h2><p id="countLabel">{h(len(changes))} élément(s) dans le journal.</p></div></section>
  <section class="list" id="list">{cards}</section>
</main>
<div class="modal" id="changeModal" role="dialog" aria-modal="true" aria-label="Détail annonce" hidden>
  <div class="modalbox">
    <button class="modalclose" id="modalClose" type="button" aria-label="Fermer">×</button>
    <div class="modalimg" id="modalImg"><span>Pas de photo</span></div>
    <div class="modalinfo">
      <div id="modalBadge" class="badge"></div>
      <h3 id="modalTitle"></h3>
      <div class="modalchips" id="modalChips"></div>
      <p id="modalSentence"></p>
      <p id="modalCycle"></p>
      <p id="modalDesc"></p>
      <div class="actions"><a id="modalLink" href="#" target="_blank" rel="noopener">Ouvrir l’annonce source</a></div>
    </div>
  </div>
</div>
<footer class="shell foot">Contrat : baseline <code>new</code> exclue, hausses conservées dans l’historique mais non mises en avant par défaut. Source technique : <code>{h(meta.get('db'))}</code>.</footer>
<script id="changesData" type="application/json">{data_json}</script>
<script>
const q=document.getElementById('q'); const buttons=[...document.querySelectorAll('[data-filter]')]; const cards=[...document.querySelectorAll('.change-card')]; const count=document.getElementById('countLabel'); const zoneFilter=document.getElementById('zoneFilter'); const communeFilter=document.getElementById('communeFilter'); const regionFilter=document.getElementById('regionFilter'); const payload=JSON.parse(document.getElementById('changesData').textContent||'{{"changes":[]}}'); const byId=new Map((payload.changes||[]).map(c=>[String(c.event_id),c])); let filter='all';
function norm(s){{return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'')}}
function esc(s){{return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}}
function fmt(v){{const n=Number(v); return Number.isFinite(n)?new Intl.NumberFormat('fr-FR').format(n)+' €':'prix n.c.'}}
function typeOk(card){{return filter==='all'||(filter==='price_down'&&card.dataset.type==='price_changed'&&card.dataset.direction==='down')||(filter==='price_up'&&card.dataset.type==='price_changed'&&card.dataset.direction==='up')||card.dataset.type===filter}}
function apply(){{const query=norm(q.value); const z=norm(zoneFilter?.value||''); const c=norm(communeFilter?.value||''); const r=norm(regionFilter?.value||''); let n=0; cards.forEach(card=>{{const okType=typeOk(card); const okQ=!query||norm(card.dataset.q).includes(query); const okZ=!z||norm(card.dataset.zone).includes(z); const okC=!c||norm(card.dataset.commune)===c; const okR=!r||norm(card.dataset.region)===r; const show=okType&&okQ&&okZ&&okC&&okR; card.hidden=!show; if(show)n++;}}); if(count) count.textContent=n+' élément(s) visible(s).';}}
function eventLabel(c){{if(c.event_type==='price_changed')return c.direction==='down'?'Baisse de prix':c.direction==='up'?'Hausse de prix':'Prix modifié'; if(c.event_type==='reappeared')return 'Annonce réapparue'; if(c.event_type==='disappeared')return 'Annonce disparue'; return c.event_type||'Changement'}}
function cycleHtml(c){{const l=c.lifecycle||{{}}; const bits=[]; if(l.last_disappeared_at)bits.push('Dernière disparition: '+String(l.last_disappeared_at).slice(0,10)); if(l.offline_days!=null)bits.push('Hors ligne: '+l.offline_days+' j'); if(l.next_reappeared_at)bits.push('Réapparition suivante: '+String(l.next_reappeared_at).slice(0,10)); if(l.status_event_count)bits.push(l.status_event_count+' évènement(s) statut'); if(l.price_event_count)bits.push(l.price_event_count+' évènement(s) prix'); if(l.nearby_price_change){{const p=l.nearby_price_change; bits.push('Prix proche: '+fmt(p.old_value)+' → '+fmt(p.new_value)+(p.direction?' · '+p.direction:''));}} return bits.length?'<p><b>Cycle annonce</b><br>'+bits.map(esc).join('<br>')+'</p>':'<p><b>Cycle annonce</b><br>Aucun cycle apparition/disparition supplémentaire dans le journal exporté.</p>'}}
function openModal(id){{const c=byId.get(String(id)); if(!c)return; const item=c.item||{{}}; const m=document.getElementById('changeModal'); document.getElementById('modalBadge').textContent=eventLabel(c); document.getElementById('modalTitle').textContent=item.title||'Annonce'; document.getElementById('modalSentence').textContent=(c.classification_note?c.classification_note+' ':'') + (document.querySelector(`[data-event-id="${{CSS.escape(String(id))}}"] .sentence`)?.textContent||''); document.getElementById('modalCycle').innerHTML=cycleHtml(c); document.getElementById('modalDesc').textContent=item.description||item.decision_summary||'Description source non disponible dans le journal.'; const chips=[item.region,item.commune,...(item.zones||[]),item.property_type,item.furnished].filter(Boolean); document.getElementById('modalChips').innerHTML=chips.map(x=>'<span>'+esc(x)+'</span>').join(''); const img=document.getElementById('modalImg'); img.innerHTML=item.image_url?`<img src="${{esc(item.image_url)}}" alt="">`:'<span>Pas de photo</span>'; const link=document.getElementById('modalLink'); link.href=item.url||'#'; m.hidden=false; m.classList.add('open');}}
function closeModal(){{const m=document.getElementById('changeModal'); m.classList.remove('open'); m.hidden=true;}}
buttons.forEach(b=>b.addEventListener('click',()=>{{filter=b.dataset.filter; buttons.forEach(x=>x.classList.toggle('active',x===b)); apply();}})); [q,zoneFilter,communeFilter,regionFilter].forEach(el=>el&&el.addEventListener('input',apply)); document.querySelectorAll('.card-open').forEach(b=>b.addEventListener('click',()=>openModal(b.dataset.eventId))); document.getElementById('modalClose')?.addEventListener('click',closeModal); document.getElementById('changeModal')?.addEventListener('click',e=>{{if(e.target.id==='changeModal')closeModal();}}); document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeModal();}}); apply();
</script>
</body></html>"""
    out_path.write_text(html, encoding="utf-8")
    return out_path


def export_changes_html(db_path: Path = DEFAULT_DB, json_out: Path = DEFAULT_OUT, html_out: Path = DEFAULT_HTML_OUT, limit: int = 80) -> dict[str, Any]:
    payload = export_changes(db_path=db_path, out_path=json_out, limit=limit)
    render_changes_html(payload, html_out)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--html-out", default=str(DEFAULT_HTML_OUT))
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--limit", type=int, default=80)
    args = ap.parse_args()
    payload = export_changes(Path(args.db), Path(args.out), args.limit)
    if not args.no_html:
        render_changes_html(payload, Path(args.html_out))
    print(json.dumps({"ok": payload.get("meta", {}).get("ok"), "out": str(args.out), "html": None if args.no_html else str(args.html_out), "changes": len(payload.get("changes") or []), "summary": payload.get("summary")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
