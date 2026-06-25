#!/usr/bin/env python3
"""Export a small public/admin-friendly changes journal from listing history.

Non-destructive: reads history.sqlite, writes artifacts/app/changes.json and
optionally artifacts/app/changes.html by default.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
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
    if u.startswith('/thumbs/'):
        return u.lstrip('/')
    return u


def item_public(raw: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback or {}
    return {
        "id": raw.get("id") or fallback.get("listing_id"),
        "title": raw.get("title") or fallback.get("title") or "Annonce",
        "url": raw.get("url") or fallback.get("url"),
        "source_site": raw.get("source_site"),
        "region": raw.get("region"),
        "commune": raw.get("commune"),
        "primary_zone": raw.get("primary_zone"),
        "location_label": raw.get("location_label") or raw.get("commune") or raw.get("region"),
        "rent_eur": as_int(raw.get("rent_eur")),
        "surface_m2": raw.get("surface_m2"),
        "rooms": raw.get("rooms"),
        "bedrooms": raw.get("bedrooms"),
        "image_url": portable_image_url(raw.get("local_image_url") or raw.get("image_url")),
    }


def classify(row: sqlite3.Row, item: dict[str, Any]) -> dict[str, Any]:
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
        changes.append(classify(row, item))

    by_type = Counter(c["event_type"] for c in changes)
    price_drops = sum(1 for c in changes if c.get("event_type") == "price_changed" and c.get("direction") == "down")
    latest_event_at = changes[0]["event_at"] if changes else None
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
    return html_lib.escape(str(value or ""), quote=True)


def fmt_eur(value: Any) -> str:
    iv = as_int(value)
    return f"{iv:,}".replace(",", " ") + " €" if iv is not None else "prix n.c."


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
    if et == "price_changed":
        delta = change.get("delta_eur")
        delta_txt = ""
        if isinstance(delta, int):
            delta_txt = f" ({delta:+,} €)".replace(",", " ")
        return f"{fmt_eur(change.get('old_value'))} → {fmt_eur(change.get('new_value'))}{delta_txt}"
    if et == "disappeared":
        return "La source ne présente plus cette annonce dans le dernier snapshot."
    if et == "reappeared":
        return "L’annonce est revenue dans le périmètre public."
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
    img_html = f'<img src="{h(image)}" alt="" loading="lazy" onerror="this.closest(\'.thumb\').classList.add(\'noimg\');this.remove()">' if image else '<span>Pas de photo</span>'
    return f"""
    <article class="change-card" data-type="{h(change.get('event_type'))}" data-q="{h(' '.join([title, loc, source]))}">
      <div class="thumb">{img_html}</div>
      <div class="change-body">
        <div class="rowline"><span class="badge {type_badge(change)}">{h(event_label(change))}</span><span class="date">{h(when)}</span></div>
        <h2>{h(title)}</h2>
        <p class="location">{h(loc)} · {h(source)}</p>
        <p class="sentence">{h(event_sentence(change))}</p>
        <div class="meta-line">
          <span>{fmt_eur(item.get('rent_eur'))}</span>
          <span>{h(item.get('surface_m2') or '?')} m²</span>
          <span>{h(item.get('rooms') or '?')} pièces</span>
        </div>
        <div class="actions"><a href="{h(url)}" target="_blank" rel="noopener">Ouvrir l’annonce source</a></div>
      </div>
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
@media(max-width:760px){{.shell{{padding:0 14px}}.nav{{align-items:flex-start}}.nav div:last-child{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}}.hero{{display:block;padding:32px 0 18px}}h1{{font-size:40px;letter-spacing:-1.4px}}.panel{{margin-top:18px}}.stats{{grid-template-columns:repeat(2,1fr);gap:8px}}.toolbar{{display:block}}.filters{{margin-top:10px}}.search{{width:100%;min-width:0}}.change-card{{grid-template-columns:96px 1fr;gap:10px;padding:8px;border-radius:15px}}.thumb{{min-height:108px}}.change-body h2{{font-size:15px;margin:6px 0 4px}}.sentence{{font-size:13px;margin:7px 0}}.location,.date,.meta-line span,.actions a{{font-size:11px}}.section-title{{display:block}}}}
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
  <section class="toolbar" aria-label="Filtres">
    <input class="search" id="q" placeholder="Filtrer: Moufia, OFIM, Saint-Denis…" autocomplete="off">
    <div class="filters"><button data-filter="all" class="active">Tout</button><button data-filter="price_changed">Prix</button><button data-filter="disappeared">Disparues</button><button data-filter="reappeared">Réapparues</button></div>
  </section>
  <section class="section-title"><div><h2>Changements récents</h2><p id="countLabel">{h(len(changes))} élément(s) dans le journal.</p></div></section>
  <section class="list" id="list">{cards}</section>
</main>
<footer class="shell foot">Contrat : baseline <code>new</code> exclue, hausses conservées dans l’historique mais non mises en avant par défaut. Source technique : <code>{h(meta.get('db'))}</code>.</footer>
<script id="changesData" type="application/json">{data_json}</script>
<script>
const q=document.getElementById('q'); const buttons=[...document.querySelectorAll('[data-filter]')]; const cards=[...document.querySelectorAll('.change-card')]; const count=document.getElementById('countLabel'); let filter='all';
function norm(s){{return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'')}}
function apply(){{const query=norm(q.value); let n=0; cards.forEach(card=>{{const okType=filter==='all'||card.dataset.type===filter; const okQ=!query||norm(card.dataset.q).includes(query); const show=okType&&okQ; card.hidden=!show; if(show)n++;}}); if(count) count.textContent=n+' élément(s) visible(s).';}}
buttons.forEach(b=>b.onclick=()=>{{filter=b.dataset.filter; buttons.forEach(x=>x.classList.toggle('active',x===b)); apply();}}); q.addEventListener('input',apply); apply();
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
