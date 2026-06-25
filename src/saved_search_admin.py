#!/usr/bin/env python3
"""Static saved-search cockpit for the Réunion immo dashboard.

Generates:
- saved_searches_admin.json: current saved-search dry-run counts + samples
- saved_searches.html: mobile-friendly cockpit to inspect searches, create a
  draft from filters, and copy/download a validated config JSON.

No server writes are exposed publicly. The page is a safe admin/workbench: it can
produce JSON, but production config still changes via file deployment.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from search_alerts import item_score, matches, search_url, sort_items

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "saved_searches.json"
DEFAULT_LISTINGS = ROOT / "artifacts" / "app" / "listings.json"
DEFAULT_OUT = ROOT / "artifacts" / "app" / "saved_searches_admin.json"
DEFAULT_HTML = ROOT / "artifacts" / "app" / "saved_searches.html"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sample_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "rent_eur": item.get("rent_eur", item.get("price")),
        "surface_m2": item.get("surface_m2", item.get("surface")),
        "rooms": item.get("rooms"),
        "bedrooms": item.get("bedrooms"),
        "location_label": item.get("location_label") or item.get("commune") or item.get("region"),
        "source_site": item.get("source_site"),
        "score": item_score(item),
        "url": item.get("url"),
        "decision_summary": item.get("decision_summary"),
    }


def build_payload(config_path: Path = DEFAULT_CONFIG, listings_path: Path = DEFAULT_LISTINGS) -> dict[str, Any]:
    cfg = load_json(config_path)
    data = load_json(listings_path)
    listings = [x for x in data.get("listings", []) if x.get("db_is_canonical") is not False]
    base_url = cfg.get("public_base_url", "/")
    searches_out = []
    for search in cfg.get("searches", []) or []:
        filters = search.get("filters") or {}
        matched = [x for x in listings if matches(x, filters)]
        min_score = search.get("min_score", cfg.get("min_score"))
        if min_score is not None:
            matched = [x for x in matched if (item_score(x) or 0) >= int(min_score)]
        matched = sort_items(matched, filters.get("sort") or "score")
        avg_score = round(sum((item_score(x) or 0) for x in matched) / len(matched), 1) if matched else 0
        searches_out.append({
            "id": search.get("id"),
            "name": search.get("name") or search.get("id"),
            "enabled": search.get("enabled", True),
            "description": search.get("description", ""),
            "filters": filters,
            "match_count": len(matched),
            "avg_score": avg_score,
            "url": search_url(base_url, filters),
            "samples": [sample_item(x) for x in matched[:8]],
            "alert_policy": {
                "new_listings": True,
                "price_drops": True,
                "disappeared": True,
                "reappeared": True,
                "price_increases": bool(search.get("notify_price_increases", cfg.get("notify_price_increases", False))),
                "max_items_per_search": int(search.get("max_items_per_search", cfg.get("max_items_per_search", 6))),
            },
        })
    return {
        "ok": True,
        "source_json": str(listings_path),
        "config_path": str(config_path),
        "public_base_url": base_url,
        "version": cfg.get("version", 1),
        "search_count": len(searches_out),
        "searches": searches_out,
        "template_search": {
            "id": "nouvelle_recherche",
            "name": "Nouvelle recherche",
            "enabled": True,
            "description": "À adapter puis copier dans config/saved_searches.json",
            "filters": {"region": [], "zones": [], "rentMax": None, "surfaceMin": None, "tab": "all", "sort": "score"},
        },
    }


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def render_html(payload: dict[str, Any], out_path: Path = DEFAULT_HTML) -> None:
    embedded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    cards = []
    for s in payload.get("searches", []):
        samples = "".join(
            f"<li><b>{esc(x.get('rent_eur') or 'n.c.')}€</b> · {esc(x.get('surface_m2') or 'n.c.')}m² · {esc(x.get('location_label'))}<br><span>{esc(x.get('title'))}</span></li>"
            for x in s.get("samples", [])[:4]
        ) or "<li>Aucun match actuel</li>"
        cards.append(f"""
        <article class="search-card">
          <div class="head"><div><h2>{esc(s.get('name'))}</h2><p>{esc(s.get('description'))}</p></div><b>{esc(s.get('match_count'))}</b></div>
          <div class="policy">Alertes: nouvelles · baisses · disparues · réapparues · hausses={'oui' if s.get('alert_policy',{}).get('price_increases') else 'non'}</div>
          <a href="{esc(s.get('url'))}" target="_blank" rel="noopener">Ouvrir la recherche</a>
          <details><summary>Échantillon + filtres JSON</summary><ul>{samples}</ul><pre>{esc(json.dumps(s.get('filters'), ensure_ascii=False, indent=2))}</pre></details>
        </article>""")
    html_doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Recherches sauvegardées</title>
<style>
:root{{--bg:#fffaf6;--ink:#211f1d;--muted:#756f68;--line:#e8ded2;--accent:#ff385c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink)}}main{{max-width:1050px;margin:auto;padding:26px 16px 70px}}h1{{font-size:clamp(32px,5vw,56px);line-height:.94;letter-spacing:-2px;margin:0}}.sub{{color:var(--muted);line-height:1.45;max-width:760px}}.top{{display:flex;justify-content:space-between;gap:18px;align-items:start;margin-bottom:20px}}.actions,.builder-actions{{display:flex;gap:8px;flex-wrap:wrap}}a,button{{border:1px solid var(--line);background:#fff;border-radius:999px;padding:10px 13px;text-decoration:none;color:inherit;font-weight:780;cursor:pointer;min-height:42px}}button.primary{{background:var(--accent);color:#fff;border-color:var(--accent)}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}.search-card,.builder{{background:#fff;border:1px solid var(--line);border-radius:24px;padding:18px;box-shadow:0 2px 8px rgba(0,0,0,.035)}}.head{{display:flex;justify-content:space-between;gap:12px}}.head h2{{margin:.1rem 0;font-size:22px}}.head p,.policy,li span{{color:var(--muted)}}.head b{{font-size:34px;color:var(--accent)}}pre{{white-space:pre-wrap;background:#f7f2ec;border-radius:16px;padding:12px;overflow:auto}}.builder{{margin:18px 0}}textarea{{width:100%;min-height:190px;border:1px solid var(--line);border-radius:18px;padding:12px;font:13px ui-monospace,monospace}}input{{width:100%;border:1px solid var(--line);border-radius:14px;padding:10px}}.formgrid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:10px 0}}@media(max-width:760px){{.top{{display:block}}.grid,.formgrid{{grid-template-columns:1fr}}}}
</style></head><body><main><section class="top"><div><h1>Alertes & recherches</h1><p class="sub">Cockpit statique: inspecte les recherches, vérifie les volumes, prépare une nouvelle config. Par sécurité, cette page ne modifie pas le serveur directement; elle copie/télécharge le JSON à déployer.</p></div><div class="actions"><a href="/">Dashboard</a><a href="/saved_searches_admin.json">JSON dry-run</a><a href="/changes.html">Changements</a></div></section><section class="builder"><h2>Créer une recherche depuis critères rapides</h2><div class="formgrid"><label>Nom<input id="name" value="Recherche mobile"></label><label>Région<input id="region" placeholder="Nord"></label><label>Zone<input id="zone" placeholder="Moufia"></label><label>Loyer max<input id="rentMax" type="number" placeholder="1000"></label><label>Surface min<input id="surfaceMin" type="number" placeholder="60"></label><label>Texte<input id="q" placeholder="jardin, T3..."></label></div><div class="builder-actions"><button class="primary" id="build">Générer JSON</button><button id="copy">Copier</button><button id="download">Télécharger</button></div><textarea id="draft" spellcheck="false"></textarea></section><section class="grid">{''.join(cards)}</section></main><script id="payload" type="application/json">{embedded}</script><script>
const payload=JSON.parse(document.getElementById('payload').textContent); const draft=document.getElementById('draft');
function slug(s){{return String(s||'recherche').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'').slice(0,48)||'recherche'}}
function build(){{const name=document.getElementById('name').value||'Nouvelle recherche'; const f={{region:[],zones:[],property_type:['Appartement','Maison','Appartement / maison'],furnished:['Non meublé','Non précisé'],tab:'all',sort:'score'}}; const r=document.getElementById('region').value.trim(); const z=document.getElementById('zone').value.trim(); if(r)f.region=[r]; if(z)f.zones=[z]; ['rentMax','surfaceMin'].forEach(k=>{{const v=document.getElementById(k).value;if(v)f[k]=Number(v)}}); const q=document.getElementById('q').value.trim(); if(q)f.q=q; const obj={{id:slug(name),name,enabled:true,description:'Créée depuis le cockpit statique',filters:f}}; draft.value=JSON.stringify(obj,null,2)}}
document.getElementById('build').onclick=build; document.getElementById('copy').onclick=async()=>{{if(!draft.value)build(); await navigator.clipboard.writeText(draft.value); alert('JSON copié')}}; document.getElementById('download').onclick=()=>{{if(!draft.value)build(); const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([draft.value],{{type:'application/json'}})); a.download='saved-search-draft.json'; a.click()}}; build();
</script></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--listings", type=Path, default=DEFAULT_LISTINGS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--html-out", type=Path, default=DEFAULT_HTML)
    args = ap.parse_args()
    payload = build_payload(args.config, args.listings)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    render_html(payload, args.html_out)
    print(json.dumps({"ok": True, "out": str(args.out), "html": str(args.html_out), "search_count": payload["search_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
