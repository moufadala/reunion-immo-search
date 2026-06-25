#!/usr/bin/env python3
from __future__ import annotations

import json, hashlib, shutil, statistics, re
from pathlib import Path
from datetime import datetime, timezone

import os

ROOT = Path('/opt/data/projects/reunion-immo-search')
SRC_APP = Path(os.environ.get('IMMO_APP_PATH', str(ROOT / 'artifacts/app')))
SRC_JSON = SRC_APP / 'listings.json'
OUT = Path(os.environ.get('IMMO_OUT_PATH', str(ROOT / 'artifacts/app_clean_v1')))
THUMBS = SRC_APP / 'thumbs'

raw = json.loads(SRC_JSON.read_text(encoding='utf-8'))
items = raw.get('listings', raw if isinstance(raw, list) else [])
thumb_names = {p.name for p in THUMBS.glob('*') if p.is_file()}

# clean output
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
if THUMBS.exists():
    shutil.copytree(THUMBS, OUT / 'thumbs')

SIDECAR_FILES = [
    'changes.html', 'changes.json',
    'source_health.html', 'source_health.json',
    'dedup.html', 'dedup_groups.json',
    'opportunity.html', 'opportunity.json',
    'locations.html', 'locations.json',
    'alertes_cours.html',
    # Family-facing aliases used from the clean homepage. Keep technical filenames too
    # for backward compatibility and direct QA, but do not expose them as raw labels.
    'veille.html', 'sources.html', 'doublons.html',
    'opportunites.html', 'localisation.html', 'alertes.html',
]

for name in SIDECAR_FILES:
    src = SRC_APP / name
    if src.exists() and src.is_file():
        shutil.copy2(src, OUT / name)

ALIASES = {
    'changes.html': 'veille.html',
    'source_health.html': 'sources.html',
    'dedup.html': 'doublons.html',
    'opportunity.html': 'opportunites.html',
    'locations.html': 'localisation.html',
    'alertes_cours.html': 'alertes.html',
}
for src_name, alias_name in ALIASES.items():
    src = OUT / src_name
    alias = OUT / alias_name
    if src.exists() and not alias.exists():
        shutil.copy2(src, alias)


def find_local(url: str | None) -> str | None:
    if not url:
        return None
    h = hashlib.sha256(url.encode()).hexdigest()
    for ext in ('.jpg', '.jpeg', '.webp', '.png'):
        for L in (24, 32, 40, 64):
            name = h[:L] + ext
            if name in thumb_names:
                return f'thumbs/{name}'
    return None


def rel_asset(path: str | None) -> str | None:
    if not path:
        return None
    p = str(path).strip()
    if p.startswith('/thumbs/'):
        return p.lstrip('/')
    return p


def uniq(seq):
    out=[]
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def money_value(s: str) -> int | None:
    if not s:
        return None
    digits = re.sub(r'[^0-9]', '', s)
    return int(digits) if digits else None


def evidence(text: str, pattern: str, rule: str, flags=re.I):
    m = re.search(pattern, text or '', flags)
    if not m:
        return None
    return {'text': m.group(0)[:180], 'rule': rule}


def analyze_description(it: dict) -> dict:
    """Non-destructive semantic layer: only claims what is explicitly evidenced."""
    desc = str(it.get('description') or '')
    title = str(it.get('title') or '')
    text = f'{title}\n{desc}'
    ntext = text.lower()
    status = it.get('description_status') or ''
    warnings=[]
    if 'Synthèse' in status:
        warnings.append('description_fallback_synthetic')
    if len(desc.strip()) < 120:
        warnings.append('description_short_or_sparse')
    if re.search(r"l'annonce a bien été ajoutée à vos favoris", ntext):
        warnings.append('boilerplate_detected')
    rent_ev = evidence(text, r'(?:(?:loyer|montant du loyer)\s*:?\s*)?([0-9]{1,3}(?:[ .\u00a0][0-9]{3})+|[0-9]{3,5})\s*€\s*(CC|C\.C\.|charges? comprises?|HC|hors charges?)?', 'rent_mentioned')
    rent_match = re.search(r'([0-9]{1,3}(?:[ .\u00a0][0-9]{3})+|[0-9]{3,5})', rent_ev['text']) if rent_ev else None
    rent_value = money_value(rent_match.group(1)) if rent_match else None
    if rent_value and isinstance(it.get('price'), int) and abs(rent_value - it['price']) >= 500:
        warnings.append(f'rent_field_mismatch: field price={it["price"]}, text rent={rent_value}')
    charges_ev = evidence(text, r'charges?\s*[:=]?\s*([0-9]{1,4}(?:[,.][0-9]{1,2})?)\s*€|\+\s*([0-9]{1,4}(?:[,.][0-9]{1,2})?)\s*€\s*(?:de\s*)?(?:ch|charges?)|\bcharges? comprises?\b|\bCC\b|hors charges?|\bHC\b', 'charges')
    deposit_ev = evidence(text, r'(?:d[eé]p[oô]t de garantie|caution)\s*[:=]?\s*([0-9]{1,5}(?:[,.][0-9]{1,2})?)\s*€', 'deposit_amount')
    fees_ev = evidence(text, r'(?:honoraires?|frais d[\'’]agence)\s*(?:locataire|charge locataire|d[\'’]agence|agence)?\s*[:=]?\s*([0-9]{1,5}(?:[,.][0-9]{1,2})?)\s*€', 'agency_fees')
    avail_ev = evidence(text, r'disponible\s+(?:imm[eé]diatement|le\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:à partir du|a partir du)\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4})|libre\s+(?:imm[eé]diatement|le\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', 'availability')
    non_meuble = evidence(text, r'non[-\s]?meubl[eé]e?s?|lou[eé]\s+vide|location\s+nue?', 'furnished_negative')
    meuble = evidence(text, r'\bmeubl[eé]e?s?\b|location\s+meubl[eé]e?', 'furnished_positive') if not non_meuble else None
    parking_ev = evidence(text, r'(?:\d+\s*)?(?:places? de )?(?:parking|stationnement)|garage\s*(?:ferm[eé]|clos|privatif)?|\bbox\b', 'parking')
    outdoor=[]
    for label, pat in [('terrace', r'\bterrasse\b'), ('balcony', r'\bbalcon\b'), ('varangue', r'\bvarangue\b|\bv[eé]randa\b'), ('garden', r'\bjardin\b'), ('pool', r'\bpiscine\b')]:
        ev=evidence(text, pat, label)
        if ev: outdoor.append(ev)
    proximity=[]
    for m in re.finditer(r'(?:proche|à proximité|a proximite|à deux pas|a deux pas)\s+(?:de|des|du|d[\'’])?\s*([^.;,\n]{3,80})', text, re.I):
        proximity.append({'text': m.group(0)[:160], 'rule': 'proximity_phrase'})
    return {
        'version': 'semantics_v1',
        'source_fields': ['title','description'],
        'quality': {'description_present': bool(desc.strip()), 'description_length': len(desc.strip()), 'status': status, 'warnings': warnings},
        'financials': {
            'rent_mentioned_eur': {'value': rent_value, 'charge_basis': 'cc' if rent_ev and re.search(r'CC|charges? comprises?', rent_ev['text'], re.I) else 'hc' if rent_ev and re.search(r'HC|hors charges?', rent_ev['text'], re.I) else 'unknown', 'evidence': [rent_ev] if rent_ev else []},
            'charges': {'status': 'mentioned' if charges_ev else 'unknown', 'evidence': [charges_ev] if charges_ev else []},
            'deposit': {'amount_eur': money_value(deposit_ev['text']) if deposit_ev else None, 'evidence': [deposit_ev] if deposit_ev else []},
            'agency_fees': {'amount_eur': money_value(fees_ev['text']) if fees_ev else None, 'evidence': [fees_ev] if fees_ev else []},
        },
        'availability': {'status': 'mentioned' if avail_ev else 'unknown', 'evidence': [avail_ev] if avail_ev else []},
        'property_state': {'furnished': {'status': 'non_meuble' if non_meuble else 'meuble' if meuble else 'unknown', 'evidence': [non_meuble or meuble] if (non_meuble or meuble) else []}},
        'features': {'parking': {'present': True if parking_ev else None, 'evidence': [parking_ev] if parking_ev else []}, 'outdoor': {'evidence': outdoor}},
        'proximity': {'raw_mentions': proximity},
    }

clean=[]
for it in items:
    price = it.get('rent_eur') or it.get('price_eur') or it.get('price')
    surface = it.get('surface_m2') or it.get('surface')
    rooms = it.get('rooms')
    bedrooms = it.get('bedrooms')
    local = find_local(it.get('image_url'))
    local_primary = rel_asset(it.get('local_image_url') or local)
    local_gallery = uniq([rel_asset(x) for x in (it.get('local_image_urls') or [])] + ([local_primary] if local_primary else []))
    if not local_gallery and local_primary:
        local_gallery = [local_primary]
    external_gallery = uniq((it.get('image_urls') or []) + ([it.get('image_url')] if it.get('image_url') else []))
    loc_intel = it.get('location_intelligence') or {}
    map_point = loc_intel.get('map_point') or {}
    precise_location = loc_intel.get('precise_location_label') or it.get('location_label') or it.get('commune') or it.get('city') or ''
    clean.append({
        'id': it.get('id'),
        'source': it.get('source_site') or it.get('source') or 'source',
        'source_id': it.get('source_id'),
        'url': it.get('url'),
        'title': it.get('title') or 'Annonce immobilière',
        'city': loc_intel.get('commune_inferred') or it.get('commune') or it.get('city') or '',
        'district': loc_intel.get('district_best') or it.get('primary_zone') or it.get('city') or it.get('location_label') or '',
        'region': loc_intel.get('region_inferred') or it.get('region') or '',
        'location': precise_location,
        'location_intelligence': loc_intel,
        'map_point': map_point,
        'map_url': map_point.get('osm_url'),
        'type': it.get('property_type') or it.get('type') or 'Bien',
        'price': int(price) if isinstance(price,(int,float)) else price,
        'surface': surface,
        'rooms': rooms,
        'bedrooms': bedrooms,
        'furnished': it.get('furnished') or 'Non précisé',
        'agency': it.get('agency_or_owner') or '',
        'image_url': it.get('image_url'),
        'image_urls': external_gallery,
        'local_image_url': local_primary,
        'local_image_urls': local_gallery,
        'description': it.get('description') or '',
        'description_status': it.get('description_status') or '',
        'description_analysis': analyze_description({**it, 'price': int(price) if isinstance(price,(int,float)) else price}),
        'seen_last_at': it.get('seen_last_at') or it.get('last_seen_at') or '',
        'published_at': it.get('published_at'),
        'score': it.get('score') or it.get('db_quality_score') or 0,
        'opportunity_analysis': it.get('opportunity_analysis') or {},
        'opportunity_score': (it.get('opportunity_analysis') or {}).get('score') or it.get('opportunity_score') or it.get('score') or 0,
    })

# Sort: recommended = score desc, recent-ish then price sane
clean.sort(key=lambda x: (x.get('score') or 0, x.get('seen_last_at') or ''), reverse=True)
export = {'generated_at': datetime.now(timezone.utc).isoformat(), 'count': len(clean), 'listings': clean}
(OUT / 'listings.json').write_text(json.dumps(export, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
INDEX_FIELDS = ('id', 'source', 'source_id', 'title', 'city', 'location', 'region', 'type', 'price', 'surface', 'rooms', 'bedrooms', 'score', 'local_image_url', 'seen_last_at')
index_items = [{k: x.get(k) for k in INDEX_FIELDS if x.get(k) not in (None, '', [])} for x in clean]
index_export = {'generated_at': export['generated_at'], 'count': len(index_items), 'listings': index_items, 'note': 'Index léger pour recherche/liste mobile; les fiches complètes restent dans listings.json.'}
(OUT / 'listings_index.json').write_text(json.dumps(index_export, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

prices=[x['price'] for x in clean if isinstance(x.get('price'), int)]
surfaces=[x['surface'] for x in clean if isinstance(x.get('surface'), (int,float))]
local_count=sum(1 for x in clean if x.get('local_image_url'))
coverage={
    'count': len(clean),
    'local_primary_photos': local_count,
    'external_primary_photos': sum(1 for x in clean if x.get('image_url')),
    'gallery_photos': sum(1 for x in clean if len(x.get('local_image_urls') or []) > 1),
    'image_urls_multi': sum(1 for x in clean if len(x.get('image_urls') or []) > 1),
    'cities': len({x['city'] for x in clean if x['city']}),
    'regions': sorted({x['region'] for x in clean if x['region']}),
    'types': sorted({x['type'] for x in clean if x['type']}),
    'price_min': min(prices) if prices else None,
    'price_max': max(prices) if prices else None,
    'surface_min': min(surfaces) if surfaces else None,
    'surface_max': max(surfaces) if surfaces else None,
    'note': 'Portail propre avec photos principales locales et galeries quand disponibles; couches veille/intelligence servies en pages séparées.'
}
(OUT / 'coverage.json').write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding='utf-8')
PUBLIC_BASE = 'https://immo.148.230.103.174.sslip.io'
PUBLIC_PAGES = ['', 'veille.html', 'sources.html', 'doublons.html', 'opportunites.html', 'localisation.html', 'alertes.html']
sitemap_urls = '\n'.join(
    f"  <url><loc>{PUBLIC_BASE}/{page}</loc><changefreq>daily</changefreq><priority>{'1.0' if not page else '0.7'}</priority></url>"
    for page in PUBLIC_PAGES
)
(OUT / 'sitemap.xml').write_text(
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    + sitemap_urls + '\n</urlset>\n',
    encoding='utf-8',
)
(OUT / 'robots.txt').write_text(
    'User-agent: *\nAllow: /\nSitemap: https://immo.148.230.103.174.sslip.io/sitemap.xml\n',
    encoding='utf-8',
)

html = r'''<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Recherche immo RUN — portail propre</title>
<style>
:root{--bg:#f6f3ee;--paper:#fffdf8;--ink:#20201d;--muted:#6f6a61;--line:#e6dfd3;--brand:#0f766e;--brand2:#134e4a;--accent:#f59e0b;--danger:#b91c1c;--shadow:0 16px 45px rgba(55,45,30,.10);--r:18px}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:inherit}.top{position:sticky;top:0;z-index:20;background:rgba(246,243,238,.94);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}.bar{max-width:1180px;margin:auto;padding:16px 18px;display:flex;gap:16px;align-items:center}.logo{font-weight:850;letter-spacing:-.04em;font-size:22px}.logo span{color:var(--brand)}.search{flex:1;display:flex;background:white;border:1px solid var(--line);border-radius:999px;box-shadow:0 4px 18px rgba(0,0,0,.04);overflow:hidden}.search input{flex:1;border:0;outline:0;padding:15px 18px;font-size:16px;background:transparent}.search button,.btn{border:0;border-radius:999px;background:var(--brand);color:white;font-weight:760;padding:12px 18px;cursor:pointer}.btn.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}.btn.ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}main{max-width:1180px;margin:0 auto;padding:24px 18px 70px}.hero{display:grid;grid-template-columns:1.2fr .8fr;gap:18px;margin:10px 0 20px}.panel{background:var(--paper);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);min-width:0}.intro{padding:28px}.intro h1{font-size:clamp(30px,5vw,58px);line-height:.94;letter-spacing:-.07em;margin:0 0 14px}.intro p{color:var(--muted);font-size:17px;line-height:1.5;max-width:760px}.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;padding:18px}.stat{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px}.stat b{display:block;font-size:28px;letter-spacing:-.04em}.stat span{color:var(--muted);font-size:13px}.filters{padding:16px;margin-bottom:18px}.filter-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.field label{font-size:12px;color:var(--muted);display:block;margin:0 0 6px;font-weight:700}.field select,.field input{width:100%;border:1px solid var(--line);border-radius:12px;background:#fff;padding:12px;font-size:14px}.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.chip{border:1px solid var(--line);background:#fff;border-radius:999px;padding:9px 12px;font-weight:700;font-size:13px;cursor:pointer}.chip.active{background:var(--brand2);color:white}.toolbar{display:flex;align-items:center;justify-content:space-between;margin:18px 0 12px;gap:12px}.toolbar h2{margin:0;font-size:22px;letter-spacing:-.04em}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{background:var(--paper);border:1px solid var(--line);border-radius:20px;overflow:hidden;box-shadow:0 10px 28px rgba(55,45,30,.08);cursor:pointer;transition:.18s transform,.18s box-shadow}.card:hover{transform:translateY(-2px);box-shadow:0 18px 45px rgba(55,45,30,.13)}.photo{height:210px;background:#ddd6c9;position:relative;overflow:hidden}.photo img{width:100%;height:100%;object-fit:cover;display:block}.no-photo{height:100%;display:grid;place-items:center;text-align:center;color:#7c7265;font-weight:800;background:linear-gradient(135deg,#eee7db,#d9cfbf)}.badge{position:absolute;left:12px;top:12px;background:rgba(255,255,255,.93);border-radius:999px;padding:7px 10px;font-size:12px;font-weight:800}.fav{position:absolute;right:12px;top:12px;background:rgba(255,255,255,.93);border:0;border-radius:50%;width:38px;height:38px;font-size:18px;cursor:pointer}.fav.on{color:#dc2626}.body{padding:15px}.price{font-size:24px;font-weight:900;letter-spacing:-.04em}.loc{font-weight:800;margin-top:3px}.title{color:var(--muted);font-size:14px;line-height:1.35;min-height:38px;margin:8px 0}.meta{display:flex;gap:8px;flex-wrap:wrap}.pill{background:#f0ebe2;border:1px solid var(--line);border-radius:999px;padding:6px 9px;font-size:12px;font-weight:750}.actions{display:flex;gap:8px;margin-top:12px}.actions a,.actions button{flex:1;text-align:center;text-decoration:none;border-radius:12px;border:1px solid var(--line);padding:10px;background:#fff;font-weight:800;color:var(--ink);cursor:pointer}.empty{padding:40px;text-align:center}.modal{position:fixed;inset:0;background:rgba(20,18,15,.55);z-index:50;display:none;align-items:center;justify-content:center;padding:18px}.modal.open{display:flex}.sheet{background:var(--paper);border-radius:24px;max-width:980px;width:100%;max-height:92vh;overflow:auto;box-shadow:0 30px 90px rgba(0,0,0,.34)}.sheet-head{display:flex;justify-content:space-between;align-items:center;padding:16px 18px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--paper);z-index:2}.sheet-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;padding:18px}.detail-img{height:440px;border-radius:18px;overflow:hidden;background:#ddd6c9}.detail-img img{width:100%;height:100%;object-fit:cover}.detail h3{font-size:32px;line-height:1;margin:0 0 8px;letter-spacing:-.05em}.desc{line-height:1.55;color:#4f4a43}.trust{background:#fff7ed;border:1px solid #fed7aa;color:#7c2d12;border-radius:14px;padding:12px;font-size:13px}.hidden{display:none!important}@media(max-width:900px){.bar{flex-wrap:wrap}.search{order:3;flex-basis:100%}.hero{grid-template-columns:1fr}.filter-grid{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr 1fr}.sheet-grid{grid-template-columns:1fr}.detail-img{height:300px}}@media(max-width:580px){main{padding:14px 12px 60px}.bar{padding:12px}.intro{padding:20px}.stats{grid-template-columns:repeat(2,1fr);padding:12px}.filter-grid{grid-template-columns:1fr}.grid{grid-template-columns:1fr}.photo{height:225px}.toolbar{align-items:flex-start;flex-direction:column}.sheet{border-radius:18px}.modal{padding:8px}.detail h3{font-size:26px}}
.galleryCount{position:absolute;right:8px;bottom:8px;background:rgba(0,0,0,.72);color:#fff;border-radius:999px;padding:5px 8px;font-size:12px;font-weight:850}.detail-img{position:relative}.photoNav{position:absolute;top:50%;transform:translateY(-50%);border:0;border-radius:999px;width:42px;height:42px;background:rgba(255,255,255,.9);box-shadow:var(--shadow);font-size:28px;font-weight:900;cursor:pointer}.photoNav.prevPhoto{left:10px}.photoNav.nextPhoto{right:10px}.photoCounter{position:absolute;left:12px;bottom:12px;background:rgba(0,0,0,.7);color:#fff;border-radius:999px;padding:6px 9px;font-size:12px;font-weight:850}.photoNav[hidden],.photoCounter[hidden]{display:none}
.layerLinks{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.layerLinks a{font-size:12px;text-decoration:none;color:#385170;background:#eef4ff;border:1px solid #d7e5ff;border-radius:999px;padding:7px 10px}.layerLinks a:hover{background:#e2eeff}.mapBox{margin-top:12px;border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#fff}.mapBox iframe{display:block;width:100%;height:230px;border:0}.mapBox .mapNote{padding:9px 11px;color:var(--muted);font-size:12px}.mapLink{font-weight:800;color:var(--brand);text-decoration:none}

.modal{overscroll-behavior:contain}.sheet{overscroll-behavior:contain;-webkit-overflow-scrolling:touch}.modal[hidden]{display:none!important}@media(max-width:580px){body{overflow-x:hidden}.bar{flex-wrap:wrap}.search{min-width:100%;order:3}.modal{align-items:flex-end;padding:0}.sheet{width:100%;max-height:100dvh;border-radius:18px 18px 0 0}.sheet-grid{grid-template-columns:1fr}.detail-img img{max-height:46dvh;object-fit:contain}.photoNav{width:48px;height:48px}.btn,.search button{min-height:44px}}
</style>
</head>
<body>
<header class="top"><div class="bar"><div class="logo">Recherche immo <span>RUN</span></div><div class="search"><input id="q" placeholder="Ville, quartier, critères… ex: Sainte-Marie T2 1000"/><button id="searchBtn">Rechercher</button></div><button class="btn secondary" id="copySearchBtn">Copier recherche</button><button class="btn secondary" id="resetBtn">Réinitialiser</button></div></header>
<main>
<section class="hero"><div class="panel intro"><h1>Un portail immo simple, alimenté par notre base scrapée.</h1><p>V1 propre façon portail classique : chercher, filtrer, comparer, ouvrir la source. Pas de debug, pas de fouillis. Les photos principales locales sont utilisées quand elles existent.</p><div class="layerLinks" aria-label="Couches de veille immo"><a href="veille.html">Veille</a><a href="sources.html">Sources</a><a href="doublons.html">Doublons</a><a href="opportunites.html">Opportunités</a><a href="localisation.html">Localisation</a><a href="alertes.html">Alertes</a></div></div><div class="panel stats"><div class="stat"><b id="total">—</b><span>annonces dans la base</span></div><div class="stat"><b id="shown">—</b><span>résultats affichés</span></div><div class="stat"><b id="photos">—</b><span>photos principales locales</span></div><div class="stat"><b id="updated">—</b><span>dernière génération</span></div></div></section>
<section class="panel filters"><div class="filter-grid"><div class="field"><label>Ville / commune</label><select id="city"><option value="">Toutes</option></select></div><div class="field"><label>Budget max</label><input id="maxPrice" type="number" inputmode="numeric" placeholder="ex: 1200"/></div><div class="field"><label>Type</label><select id="type"><option value="">Tous</option></select></div><div class="field"><label>Surface min.</label><input id="minSurface" type="number" inputmode="numeric" placeholder="ex: 40"/></div><div class="field"><label>Pièces min.</label><select id="minRooms"><option value="">Toutes</option><option value="1">Studio / 1+</option><option value="2">2+</option><option value="3">3+</option><option value="4">4+</option><option value="5">5+</option></select></div><div class="field"><label>Tri</label><select id="sort"><option value="recommended">Recommandé</option><option value="price_asc">Prix croissant</option><option value="price_desc">Prix décroissant</option><option value="surface_desc">Surface</option><option value="recent">Récent</option></select></div></div><div class="chips" id="chips"></div></section>
<div class="toolbar"><h2>Résultats</h2><div class="muted" id="summary">Chargement…</div></div>
<section class="grid" id="grid"></section><section class="panel empty hidden" id="empty"><h2>Aucun résultat exact</h2><p class="muted">Essaie d’élargir la zone, de retirer un quartier trop précis, ou d’augmenter le budget. Exemple : “Sainte-Marie 1000” plutôt que “La Bretagne 1000” si le quartier n’est pas présent dans les données source.</p></section>
</main>
<div class="modal" id="modal" role="dialog" aria-modal="true" aria-labelledby="mHead" hidden><div class="sheet"><div class="sheet-head"><strong id="mHead">Détail annonce</strong><button class="btn ghost" id="closeModal">Fermer</button></div><div class="sheet-grid"><div class="detail-img" id="mImg"></div><div class="detail"><h3 id="mPrice"></h3><div class="loc" id="mLoc"></div><p class="muted" id="mTitle"></p><div class="meta" id="mMeta"></div><p class="desc" id="mDesc"></p><div class="trust" id="mTrust"></div><div class="actions"><a id="mSource" target="_blank" rel="noreferrer">Ouvrir la source</a><button id="mFav">Favori</button></div></div></div></div></div>
<script>
let all=[], state={q:'',city:'',type:'',maxPrice:'',minPrice:'',minSurface:'',minRooms:'',minBedrooms:'',minScore:'',sort:'recommended',region:'',zones:''};
const urlKeys=['q','city','type','maxPrice','minPrice','minSurface','minRooms','minBedrooms','minScore','sort','region','zones'];
function safeJsonArray(value){try{const parsed=JSON.parse(value||'[]');return Array.isArray(parsed)?parsed:[];}catch(e){return [];}}
const favs=new Set(safeJsonArray(localStorage.getItem('immo_clean_favs')));
const hidden=new Set(safeJsonArray(sessionStorage.getItem('immo_clean_hidden_session'))); localStorage.removeItem('immo_clean_hidden');
const $=s=>document.querySelector(s);
const fmtPrice=v=>v?new Intl.NumberFormat('fr-FR').format(v)+' €/mois':'Prix NC';
const uniq=a=>[...new Set((a||[]).filter(Boolean))];
const gallery=x=>{const locals=uniq([...(x.local_image_urls||[]),...(x.local_image_url?[x.local_image_url]:[])]); const remotes=uniq([...(x.image_urls||[]),...(x.image_url?[x.image_url]:[])]); if(locals.length) return locals; return remotes;};
const imgSrc=x=>gallery(x)[0]||'';
let modalPhotoIndex=0, modalGallery=[];

function saveFavs(){localStorage.setItem('immo_clean_favs',JSON.stringify([...favs]));}
function norm(s){return String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim().replace(/\s+/g,' ');}
function debounce(fn,ms=220){let t;return (...args)=>{clearTimeout(t);t=setTimeout(()=>fn(...args),ms);};}
function resetSearchState(){state={q:'',city:'',type:'',maxPrice:'',minPrice:'',minSurface:'',minRooms:'',minBedrooms:'',minScore:'',sort:'recommended',region:'',zones:''};}
function readStateFromUrl(){const p=new URLSearchParams(location.search); const hasImmo=p.get('immo')==='1'||['r','z','t','rentMin','rentMax','bedroomsMin','minScore'].some(k=>p.has(k)); if(!hasImmo) return false; urlKeys.forEach(k=>{if(p.has(k)) state[k]=p.get(k)||'';}); if(p.has('r')) state.region=p.get('r')||''; if(p.has('z')) state.zones=p.get('z')||''; if(p.has('rentMin')) state.minPrice=p.get('rentMin')||''; if(p.has('rentMax')) state.maxPrice=p.get('rentMax')||''; if(p.has('bedroomsMin')) state.minBedrooms=p.get('bedroomsMin')||''; if(p.has('t')){const t=(p.get('t')||'').split('|').filter(Boolean); state.type=t.length===1?t[0]:'';} return true;}
function encodeStateParams(){const p=new URLSearchParams(location.search); urlKeys.forEach(k=>p.delete(k)); p.delete('immo'); const has=urlKeys.some(k=>state[k] && !(k==='sort' && state[k]==='recommended')); if(has){p.set('immo','1'); urlKeys.forEach(k=>{if(state[k] && !(k==='sort' && state[k]==='recommended')) p.set(k,state[k]);});} return p.toString();}
function syncUrl(){const qs=encodeStateParams(); history.replaceState(null,'',location.pathname+(qs?'?'+qs:'')+location.hash);}
function syncInputs(){['q','city','type','maxPrice','minSurface','minRooms'].forEach(id=>{$('#'+id).value=state[id]||'';}); $('#sort').value=state.sort||'recommended'; document.querySelectorAll('.chip').forEach(c=>c.classList.toggle('active',(c.dataset.region||'')===(state.region||'')));}
function esc(s){return (s??'').toString().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&#39;');}
function attr(s){return esc(s).replace(/`/g,'&#96;');}
function initFilters(data){
 const cities=[...new Set(data.map(x=>x.city).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'fr'));
 $('#city').innerHTML='<option value="">Toutes</option>'+cities.map(c=>`<option value="${attr(c)}">${esc(c)}</option>`).join('');
 const types=[...new Set(data.map(x=>x.type).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'fr'));
 $('#type').innerHTML='<option value="">Tous</option>'+types.map(c=>`<option value="${attr(c)}">${esc(c)}</option>`).join('');
 const regs=[...new Set(data.map(x=>x.region).filter(Boolean))].sort();
 $('#chips').innerHTML=['Tous',...regs].map((r,i)=>`<button class="chip ${i==0?'active':''}" data-region="${i? attr(r):''}">${esc(r)}</button>`).join('');
}
function parseQuery(q){
 let raw=norm(q).replace(/\bst\b/g,'saint').replace(/\bste\b/g,'sainte');
 let rooms=[], maxPrice=null, minSurface=null, typeHint='';
 [...raw.matchAll(/\bt\s*([1-9])\b/g)].forEach(m=>rooms.push(Number(m[1])));
 if(/\bstudio\b/.test(raw)) rooms.push(1);
 if(/\b(maison|villa)\b/.test(raw)) typeHint='maison';
 if(/\b(appartement|appart|apt)\b/.test(raw)) typeHint='appartement';
 const p=raw.match(/(?:moins de|moins|sous|max|budget|loyer|jusqu a|jusqua|<=?)\s*(\d{2,5})/); if(p) maxPrice=Number(p[1]);
 const m=raw.match(/(\d{1,3})\s*(?:m2|m²|metres|metre|m)\b/); if(m) minSurface=Number(m[1]);
 if(!maxPrice){const nums=[...raw.matchAll(/\b(\d{3,5})\b/g)].map(m=>Number(m[1])).filter(n=>n>=300&&n<=10000); if(nums.length) maxPrice=Math.max(...nums);}
 const stop=new Set(['le','la','les','l','un','une','des','du','de','d','a','au','aux','en','sur','dans','avec','sans','et','ou','pour','moins','sous','max','budget','loyer','jusqu','jusqua','m2','m','metres','metre','min','minimum','plus','piece','pieces','p','ch','chambre','chambres','appartement','appart','apt','maison','villa']);
 let tokens=raw.split(/\s+/).filter(Boolean).filter(x=>!stop.has(x) && !/^t[1-9]$/.test(x) && !/^\d{1,5}$/.test(x));
 tokens=[...new Set(tokens)]; rooms=[...new Set(rooms)];
 return {tokens, rooms, maxPrice, minSurface, typeHint};
}
function relevance(x,tokens){if(!tokens.length)return 0; const hay=x._hay||''; let score=0; for(const t of tokens){if(hay.split(' ').includes(t)) score+=3; else if(hay.includes(t)) score+=1;} return score;}
function apply(updateUrl=true){
 const parsed=parseQuery(state.q); let res=all.filter(x=>!hidden.has(x.id));
 if(parsed.tokens.length) res=res.filter(x=>parsed.tokens.every(t=>(x._hay||'').includes(t)));
 if(parsed.rooms.length) res=res.filter(x=>parsed.rooms.includes(Number(x.rooms)));
 if(parsed.typeHint && !state.type) res=res.filter(x=>norm(x.type).includes(parsed.typeHint));
 if(state.city) res=res.filter(x=>x.city===state.city);
 if(state.type) res=res.filter(x=>x.type===state.type);
 if(state.region) res=res.filter(x=>x.region===state.region);
 const maxPrice=state.maxPrice||parsed.maxPrice; if(maxPrice) res=res.filter(x=>x.price && x.price<=Number(maxPrice));
 const minPrice=state.minPrice; if(minPrice) res=res.filter(x=>x.price && x.price>=Number(minPrice));
 const minSurface=state.minSurface||parsed.minSurface; if(minSurface) res=res.filter(x=>x.surface && x.surface>=Number(minSurface));
 if(state.minRooms) res=res.filter(x=>x.rooms && Number(x.rooms)>=Number(state.minRooms));
 if(state.minBedrooms) res=res.filter(x=>x.bedrooms && Number(x.bedrooms)>=Number(state.minBedrooms));
 if(state.minScore) res=res.filter(x=>Number(x.opportunity_score||x.score||0)>=Number(state.minScore));
 if(state.zones){const zs=state.zones.split('|').map(norm).filter(Boolean); if(zs.length) res=res.filter(x=>{const hay=norm([x.city,x.district,x.location,x.title,x.description].join(' ')); return zs.some(z=>hay.includes(z));});}
 const s=state.sort;
 res.sort((a,b)=> s==='price_asc'?(a.price||9e9)-(b.price||9e9):s==='price_desc'?(b.price||0)-(a.price||0):s==='surface_desc'?(b.surface||0)-(a.surface||0):s==='recent'?String(b.seen_last_at||'').localeCompare(String(a.seen_last_at||'')):(relevance(b,parsed.tokens)*100+(b.opportunity_score||b.score||0))-(relevance(a,parsed.tokens)*100+(a.opportunity_score||a.score||0)));
 render(res); if(updateUrl) syncUrl(); return res;
}
function card(x){const imgs=gallery(x), src=imgs[0]; const more=imgs.length>1?`<span class="galleryCount">+${imgs.length-1} photos</span>`:''; const safeTitle=esc(x.title||'Annonce immobilière'); const safeLocation=esc(x.location||x.city||''); const safeSource=esc(x.source||'source'); const safeType=esc(x.type||'Bien'); const safeUrl=attr(x.url||'#'); const safeId=attr(x.id); const map=x.map_url?`<a class="mapLink" href="${attr(x.map_url)}" target="_blank" rel="noreferrer" onclick="event.stopPropagation()">Carte</a>`:''; const imgErr="this.style.display='none';if(!this.parentElement.querySelector('.no-photo')){const d=document.createElement('div');d.className='no-photo';d.textContent='Photo indisponible';this.parentElement.prepend(d);}"; return `<article class="card" data-id="${safeId}"><div class="photo">${src?`<img loading="lazy" src="${attr(src)}" alt="${attr(x.title||'Annonce immobilière')}" onerror="${attr(imgErr)}">`:'<div class="no-photo">Photo indisponible</div>'}${more}<span class="badge">${safeSource}</span><button class="fav ${favs.has(x.id)?'on':''}" data-fav="${safeId}" title="Favori">♥</button></div><div class="body"><div class="price">${fmtPrice(x.price)}</div><div class="loc">${safeLocation}</div><div class="title">${safeTitle}</div><div class="meta"><span class="pill">${safeType}</span>${x.surface?`<span class="pill">${esc(x.surface)} m²</span>`:''}${x.rooms?`<span class="pill">${esc(x.rooms)} p.</span>`:''}${x.bedrooms?`<span class="pill">${esc(x.bedrooms)} ch.</span>`:''}${x.location_intelligence?.quality?`<span class="pill">loc. ${esc(x.location_intelligence.quality)}</span>`:''}</div><div class="actions"><a href="${safeUrl}" target="_blank" rel="noreferrer" onclick="event.stopPropagation()">Source</a><button data-open="${safeId}">Analyse</button>${map}<button data-hide="${safeId}" onclick="event.stopPropagation()">Masquer</button></div></div></article>`}
function render(res){const limit=120; const shown=Math.min(res.length,limit); $('#shown').textContent=shown; $('#summary').textContent=res.length>limit?`${shown} affichés sur ${res.length} correspondances (${all.length} annonces au total)`:`${res.length} résultat${res.length>1?'s':''} sur ${all.length}`; $('#grid').innerHTML=res.slice(0,limit).map(card).join('');$('#empty').classList.toggle('hidden',res.length>0);}
function setModalPhoto(delta=0){if(!modalGallery.length)return; modalPhotoIndex=(modalPhotoIndex+delta+modalGallery.length)%modalGallery.length; const src=modalGallery[modalPhotoIndex]; const multi=modalGallery.length>1; $('#mImg').innerHTML=`<img src="${attr(src)}" alt="Photo annonce"><button class="photoNav prevPhoto" aria-label="Photo précédente" ${multi?'':'hidden'} type="button">‹</button><button class="photoNav nextPhoto" aria-label="Photo suivante" ${multi?'':'hidden'} type="button">›</button><div class="photoCounter" ${multi?'':'hidden'}>${modalPhotoIndex+1}/${modalGallery.length}</div>`; const img=$('#mImg img'); if(img) img.onerror=()=>{if(multi){setModalPhoto(1)}else{$('#mImg').innerHTML='<div class="no-photo">Photo indisponible</div>';}}; $('#mImg .prevPhoto')?.addEventListener('click',e=>{e.stopPropagation();setModalPhoto(-1)}); $('#mImg .nextPhoto')?.addEventListener('click',e=>{e.stopPropagation();setModalPhoto(1)});}
function openDetail(id){const x=all.find(i=>i.id===id); if(!x)return; modalGallery=gallery(x); modalPhotoIndex=0; $('#mHead').textContent=(x.source||'source')+' · '+(x.source_id||x.id); $('#mPrice').textContent=fmtPrice(x.price); $('#mLoc').textContent=x.location||x.city||''; $('#mTitle').textContent=x.title||'Annonce immobilière'; if(modalGallery.length){setModalPhoto(0)}else{$('#mImg').innerHTML='<div class="no-photo">Photo indisponible</div>';} $('#mMeta').innerHTML=`<span class="pill">${esc(x.type||'Bien')}</span>${x.surface?`<span class="pill">${esc(x.surface)} m²</span>`:''}${x.rooms?`<span class="pill">${esc(x.rooms)} pièces</span>`:''}${x.bedrooms?`<span class="pill">${esc(x.bedrooms)} chambres</span>`:''}<span class="pill">${esc(x.region||'Réunion')}</span>${x.location_intelligence?.district_best?`<span class="pill">quartier: ${esc(x.location_intelligence.district_best)}</span>`:''}${x.location_intelligence?.quality?`<span class="pill">précision ${esc(x.location_intelligence.quality)}</span>`:''}`; $('#mDesc').textContent=x.description||'Pas de description source disponible.'; const trust=(modalGallery.length>1?`Galerie: ${modalGallery.length} photo${modalGallery.length>1?'s':''}. `:(x.local_image_url?'Photo principale servie localement. ':'Photo externe ou indisponible. '))+(x.description_status||'Description selon source/export.'); const mp=x.map_point; const mapHtml=mp&&mp.lat?`<div class="mapBox"><iframe loading="lazy" src="https://www.openstreetmap.org/export/embed.html?bbox=${mp.lon-0.01}%2C${mp.lat-0.01}%2C${mp.lon+0.01}%2C${mp.lat+0.01}&layer=mapnik&marker=${mp.lat}%2C${mp.lon}"></iframe><div class="mapNote">${esc(mp.label||x.location)} — ${esc(mp.precision||'position approximative')} · <a href="${attr(x.map_url||mp.osm_url||'#')}" target="_blank" rel="noreferrer">ouvrir la carte</a></div></div>`:''; $('#mTrust').innerHTML=esc(trust)+mapHtml; $('#mSource').href=x.url||'#'; $('#mFav').textContent=favs.has(x.id)?'Retirer favori':'Ajouter favori'; $('#mFav').onclick=()=>{favs.has(x.id)?favs.delete(x.id):favs.add(x.id);saveFavs();openDetail(id);apply();}; openModal();}
async function boot(){const data=await fetch('listings.json',{cache:'no-store'}).then(r=>r.json()); all=data.listings; all.forEach(x=>{x._hay=norm([x.id,x.source_id,x.title,x.city,x.district,x.location,x.region,x.type,x.source,x.agency,x.url,x.description,x.rooms?('t'+x.rooms+' '+x.rooms+' pieces '+x.rooms+' p'):'',x.bedrooms?x.bedrooms+' chambres':'',x.location_intelligence?.district_best||'',x.location_intelligence?.precise_location_label||'',(x.location_intelligence?.district_hints||[]).join(' '),(x.opportunity_analysis?.label||''),(x.opportunity_analysis?.reasons||[]).join(' '),(x.opportunity_analysis?.warnings||[]).join(' ')].join(' '));}); initFilters(all); readStateFromUrl(); syncInputs(); $('#total').textContent=all.length; $('#photos').textContent=all.filter(x=>x.local_image_url).length+'/'+all.length+' · '+all.filter(x=>(x.local_image_urls||[]).length>1).length+' galeries'; $('#updated').textContent=new Date(data.generated_at).toLocaleDateString('fr-FR'); apply();}
['city','type','minRooms','sort'].forEach(id=>{$('#'+id).addEventListener('input',e=>{state[id]=e.target.value;apply();});});
const debouncedApply=debounce(()=>apply(),220); ['q','maxPrice','minSurface'].forEach(id=>{$('#'+id).addEventListener('input',e=>{state[id]=e.target.value;debouncedApply();});});
$('#searchBtn').onclick=()=>{state.q=$('#q').value;apply();}; $('#q').addEventListener('keydown',e=>{if(e.key==='Enter'){state.q=$('#q').value;apply();}});
$('#copySearchBtn').onclick=async()=>{syncUrl(); const url=location.href; try{await navigator.clipboard.writeText(url); $('#copySearchBtn').textContent='Lien copié';}catch(e){const ta=document.createElement('textarea'); ta.value=url; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); $('#copySearchBtn').textContent='Lien copié';} setTimeout(()=>$('#copySearchBtn').textContent='Copier recherche',1400);};
$('#resetBtn').onclick=()=>{resetSearchState(); hidden.clear(); sessionStorage.removeItem('immo_clean_hidden_session'); localStorage.removeItem('immo_clean_hidden'); syncInputs(); apply();};
$('#chips').onclick=e=>{const b=e.target.closest('.chip'); if(!b)return; state.region=b.dataset.region; document.querySelectorAll('.chip').forEach(c=>c.classList.remove('active')); b.classList.add('active'); apply();};
$('#grid').onclick=e=>{const fav=e.target.closest('[data-fav]'); if(fav){e.stopPropagation(); const id=fav.dataset.fav; favs.has(id)?favs.delete(id):favs.add(id); saveFavs(); apply(); return;} const open=e.target.closest('[data-open]'); if(open){openDetail(open.dataset.open); return;} const hide=e.target.closest('[data-hide]'); if(hide){hidden.add(hide.dataset.hide); sessionStorage.setItem('immo_clean_hidden_session',JSON.stringify([...hidden])); apply(); return;} const c=e.target.closest('.card'); if(c) openDetail(c.dataset.id);};
function openModal(){const m=$('#modal');m.hidden=false;m.classList.add('open');document.body.style.overflow='hidden';}
function closeModal(){const m=$('#modal');m.classList.remove('open');m.hidden=true;document.body.style.overflow='';}
$('#closeModal').onclick=closeModal; $('#modal').onclick=e=>{if(e.target.id==='modal')closeModal();}; document.addEventListener('keydown',e=>{if(!$('#modal').classList.contains('open'))return; if(e.key==='Escape')closeModal(); if(e.key==='ArrowLeft')setModalPhoto(-1); if(e.key==='ArrowRight')setModalPhoto(1);});
boot().catch(err=>{console.error(err); $('#summary').textContent='Erreur de chargement des annonces';});
</script>
</body></html>'''
(OUT / 'index.html').write_text(html, encoding='utf-8')
print(json.dumps({'out': str(OUT), 'listings': len(clean), 'local_photos': local_count, 'coverage': coverage}, ensure_ascii=False, indent=2))
