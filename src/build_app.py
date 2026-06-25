#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json, re, sqlite3, html
from pathlib import Path
from datetime import datetime, timezone
from normalize_db import refresh as refresh_product_enrichment
from listing_changes import export_changes, render_changes_html
from source_health import build_payload as build_source_health_payload, render_html as render_source_health_html
from saved_search_admin import build_payload as build_saved_search_admin_payload, render_html as render_saved_search_admin_html

import os

BASE=Path('/opt/data/projects/reunion-immo-search')
_parser = argparse.ArgumentParser()
_parser.add_argument('--db', default=os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db'))
_parser.add_argument('--out', '--app', dest='out', default=os.environ.get('IMMO_APP_PATH', str(BASE/'artifacts'/'app')))
_args = _parser.parse_args()
DB=Path(_args.db)
OUT=Path(_args.out)
OUT.mkdir(parents=True, exist_ok=True)

TARGET_ZONES=[
    ('moufia','Moufia'),
    ('la bretagne','La Bretagne'),
    ('bretagne','La Bretagne'),
    ('sainte marie','Sainte-Marie'),
    ('sainte-marie','Sainte-Marie'),
    ('saint denis','Saint-Denis'),
    ('saint-denis','Saint-Denis'),
]

COMMUNES=['Saint-Denis','Sainte-Marie','Sainte-Clotilde','Le Tampon','Saint-Pierre','Saint-Paul','La Possession','Saint-Leu','Saint-André','Saint-Louis','Le Port','Sainte-Suzanne','Saint-Benoît','Saint-Joseph','Bras-Panon','Entre-Deux','Les Avirons','Etang-Salé','La Plaine des Palmistes','Saint-Gilles les Bains','La Saline']
COMMUNE_ALIASES={
    'sainte clotilde':'Saint-Denis', 'saint clotilde':'Saint-Denis', 'moufia':'Saint-Denis', 'la bretagne':'Saint-Denis', 'montgaillard':'Saint-Denis', 'la montagne':'Saint-Denis', 'chaudron':'Saint-Denis',
    'ste marie':'Sainte-Marie', 'sainte marie':'Sainte-Marie', 'st denis':'Saint-Denis', 'saint denis':'Saint-Denis', 'st pierre':'Saint-Pierre', 'saint pierre':'Saint-Pierre',
    'st paul':'Saint-Paul', 'saint paul':'Saint-Paul', 'st leu':'Saint-Leu', 'saint leu':'Saint-Leu', 'saint andre':'Saint-André', 'saint benoit':'Saint-Benoît', 'étang salé':'Etang-Salé', 'entre deux':'Entre-Deux'
}
REGION_BY_COMMUNE={
    'Nord': {'Saint-Denis','Sainte-Marie','Sainte-Clotilde','Sainte-Suzanne'},
    'Est': {'Saint-André','Saint-Benoît','Bras-Panon','La Plaine des Palmistes','Sainte Anne'},
    'Ouest': {'Saint-Paul','La Possession','Le Port','Saint-Leu','La Saline','Saint-Gilles les Bains','Saint Gilles Les Hauts'},
    'Sud': {'Saint-Pierre','Le Tampon','Saint-Louis','Saint-Joseph','Etang-Salé','Entre-Deux','Les Avirons','Petite Ile'}
}

def clean(s):
    return re.sub(r'\s+',' ',str(s or '')).strip()

def norm(s):
    return clean(s).lower().replace('-', ' ')

def detect_zone(row):
    text=norm(' '.join(str(row.get(k) or '') for k in ['title','city','district','description','url']))
    zones=[]
    if 'moufia' in text: zones.append('Moufia')
    if 'la bretagne' in text or re.search(r'\bbretagne\b', text): zones.append('La Bretagne')
    if 'sainte marie' in text or 'saint marie' in text: zones.append('Sainte-Marie')
    # Saint-Denis only if not just Sainte-Marie etc.
    if 'saint denis' in text or 'sainte clotilde' in text or 'montgaillard' in text or 'la montagne' in text:
        zones.append('Saint-Denis')
    # Prefer granular zones first
    order=['Moufia','La Bretagne','Sainte-Marie','Saint-Denis']
    zones=[z for z in order if z in zones]
    if zones: return zones[0], zones
    city=clean(row.get('city'))
    if city: return city, [city]
    return 'Zone non précisée', []

def detect_commune(row):
    text=norm(' '.join(str(row.get(k) or '') for k in ['city','district','title','description','url']))
    for alias, commune in COMMUNE_ALIASES.items():
        if alias in text:
            return commune
    for c in COMMUNES:
        if norm(c) in text:
            return c
    return clean(row.get('city')) or 'Non précisée'

def region_for(commune):
    commune=clean(commune)
    for region, communes in REGION_BY_COMMUNE.items():
        if commune in communes:
            return region
    return 'Région non précisée'

def location_label(row, commune, primary_zone):
    bits=[]
    district=clean(row.get('district'))
    if district and district.lower() not in ['none','nan']:
        bits.append(district)
    if primary_zone and primary_zone not in ['Zone non précisée', commune] and primary_zone not in bits:
        bits.append(primary_zone)
    if commune and commune!='Non précisée' and commune not in bits:
        bits.append(commune)
    return ' · '.join(bits) if bits else 'Secteur non précisé'

def description_pack(row, item):
    raw=clean(row.get('description'))
    title=clean(row.get('title'))
    # V3: never truncate genuine source descriptions in the data payload. If the UI
    # needs a preview, it must shorten client-side; the JSON keeps the source text.
    if raw and len(raw) >= 45 and raw.lower() != title.lower():
        return raw, 'Description source'
    parts=[]
    if item.get('property_type'): parts.append(item['property_type'])
    if item.get('rooms'): parts.append(f"{item['rooms']} pièce(s)")
    if item.get('bedrooms'): parts.append(f"{item['bedrooms']} chambre(s)")
    if item.get('surface_m2'): parts.append(f"{item['surface_m2']:g} m²")
    if item.get('rent_eur'): parts.append(f"{item['rent_eur']} €/mois")
    loc=item.get('location_label') or item.get('primary_zone') or item.get('commune')
    synth=(f"{title}. " if title else '') + f"Repères extraits: {', '.join(parts)}. Secteur: {loc}. Source: {item.get('source_site','n.c.')}"
    return synth, 'Synthèse depuis titre + champs'

def decision_summary(item):
    reasons=[]
    if item.get('region') and item['region']!='Région non précisée': reasons.append(item['region'])
    if item.get('primary_zone') in ['Moufia','La Bretagne','Sainte-Marie']: reasons.append('zone prioritaire')
    elif item.get('primary_zone')=='Saint-Denis': reasons.append('secteur cible large')
    if item.get('rent_eur') and 600 <= item['rent_eur'] <= 1000: reasons.append('budget cible')
    elif item.get('rent_eur') and item['rent_eur'] <= 1200: reasons.append('budget proche')
    if item.get('surface_m2') and item['surface_m2'] >= 60: reasons.append('surface confortable')
    if item.get('furnished')=='Non meublé': reasons.append('non meublé')
    return ' · '.join(reasons[:4]) or 'à vérifier manuellement'

def furnished_status(row):
    text=norm(' '.join(str(row.get(k) or '') for k in ['title','description','url']))
    # Detect explicit negatives before positives; "non meublé" contains "meublé".
    if re.search(r'non\s*[- ]?meubl|lou[ée]\s+vide|logement\s+vide|location\s+nue?\b|\bnu\b|\bvide\b', text): return 'Non meublé'
    if re.search(r'\bmeubl[ée]e?s?\b|furnished', text): return 'Meublé'
    return 'Non précisé'

def property_label(ptype, title):
    t=norm((ptype or '')+' '+(title or ''))
    if any(x in t for x in ['maison','villa','house']): return 'Maison'
    if any(x in t for x in ['appartement','studio','flat','apartment','duplex','t1','t2','t3','t4','t5']): return 'Appartement'
    return 'Appartement / maison'

HARD_NON_RESIDENTIAL_RE = re.compile(r'\b(local\s+(?:commercial|professionnel|m[eé]dical)|locaux\s+commerciaux|bureau(?:x)?\b|box\b|parking\b|garage\b|garde\s*-?\s*meuble|terrain\b|entrep[oô]t)\b', re.I)

def residential_status(row):
    """Classify residentiality with the same conservative semantics as the portfolio gate.

    Do not reject a residential listing just because its long description mentions
    incidental parking, a home office/bureau, or an agent commercial.
    """
    title=clean(row.get('title'))
    desc=clean(row.get('description'))
    ptype=norm(row.get('property_type'))
    url=clean(row.get('url'))
    reasons=[]
    if ptype in {'commercial','box'}:
        reasons.append(f'property_type={ptype}')
    strong_text=' '.join([title, ptype, url])
    m=HARD_NON_RESIDENTIAL_RE.search(strong_text)
    if m:
        reasons.append(f'strong_keyword={m.group(1)}')
    dm=re.search(r'\b(local\s+(?:commercial|professionnel|m[eé]dical)|locaux\s+commerciaux)\b', desc, re.I)
    residential_hint = re.search(r'\b(maison|villa|appartement|studio|duplex|t[1-6]|f[1-6])\b', ' '.join([title, ptype]), re.I)
    incidental_local = re.search(r'(possibilit[eé].{0,80}local\s+m[eé]dical|am[eé]nag[eé]e?.{0,80}cabinet\s+m[eé]dical)', desc, re.I)
    if dm and not (residential_hint and incidental_local):
        reasons.append(f'description_keyword={dm.group(1)}')
    return ('suspect_non_residential' if reasons else 'residential_candidate'), reasons

def is_residential(row):
    return residential_status(row)[0] == 'residential_candidate'

def score_default(item):
    score=0
    if item['primary_zone'] in ['Moufia','La Bretagne','Sainte-Marie']: score+=40
    elif item['primary_zone']=='Saint-Denis': score+=18
    if item['surface_m2'] and item['surface_m2']>=60: score+=16
    if item['rent_eur'] and 600<=item['rent_eur']<=1000: score+=18
    if item['bedrooms'] and item['bedrooms']>=1: score+=10
    elif item['rooms'] and item['rooms']>=2: score+=8
    if item['furnished']=='Non meublé': score+=8
    elif item['furnished']=='Non précisé': score+=3
    if item['image_url']: score+=10
    return score

def variant_tags(item):
    tags=[]
    in_target=item['primary_zone'] in ['Moufia','La Bretagne','Sainte-Marie','Saint-Denis']
    if in_target: tags.append('Zone ciblée')
    if item['primary_zone'] in ['Moufia','La Bretagne','Sainte-Marie']: tags.append('Zone prioritaire')
    if item['rent_eur'] and 600<=item['rent_eur']<=1000: tags.append('Budget 600–1000€')
    elif item['rent_eur'] and item['rent_eur']<=1200: tags.append('Budget proche')
    if item['surface_m2'] and item['surface_m2']>=60: tags.append('≥ 60 m²')
    elif item['surface_m2'] and item['surface_m2']>=50: tags.append('Surface proche')
    if item['furnished']=='Meublé': tags.append('Variante meublée')
    if not in_target and item['rent_eur'] and 600<=item['rent_eur']<=1000 and item['surface_m2'] and item['surface_m2']>=60: tags.append('Même budget/surface ailleurs')
    return tags[:4]

enrichment_report=refresh_product_enrichment(DB)
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
rows=[dict(r) for r in con.execute("SELECT * FROM rental_listings_product WHERE COALESCE(is_active,1)=1 AND image_url IS NOT NULL AND TRIM(image_url)!=''")]
def build_item(r):
    db_zones=[]
    if r.get('zones_json'):
        try: db_zones=json.loads(r.get('zones_json') or '[]')
        except json.JSONDecodeError: db_zones=[]
    primary_zone=clean(r.get('zone_normalized')) if r.get('zone_normalized') else None
    commune=clean(r.get('city_normalized')) if r.get('city_normalized') else None
    if not primary_zone or not commune:
        primary_zone, db_zones = detect_zone(r)
        commune = detect_commune(r)
    zones=db_zones or ([primary_zone] if primary_zone and primary_zone!='Zone non précisée' else [])
    region=clean(r.get('region')) if r.get('region') else region_for(commune)
    item={
        'id': f"{r['source_site']}:{r['source_id']}",
        'source_site': r['source_site'],
        'source_id': r['source_id'],
        'url': r['url'],
        'title': clean(r['title']) or 'Annonce sans titre',
        'city': clean(r['city']) or commune,
        'commune': commune,
        'region': region,
        'primary_zone': primary_zone,
        'zones': zones,
        'property_type': clean(r.get('property_type_normalized')) or property_label(r.get('property_type'), r.get('title')),
        'rooms': r.get('rooms'),
        'bedrooms': r.get('bedrooms') or (max((r.get('rooms') or 1)-1, 0) if r.get('rooms') else None),
        'surface_m2': float(r['surface_m2']) if r.get('surface_m2') is not None else None,
        'rent_eur': int(r['rent_eur']) if r.get('rent_eur') is not None else None,
        'furnished': furnished_status(r),
        'agency_or_owner': clean(r.get('agency_or_owner')),
        'image_url': r['image_url'],
        'seen_last_at': r.get('seen_last_at'),
        'published_at': r.get('published_at'),
    }
    item['location_label']=location_label(r, commune, primary_zone)
    item['description'], item['description_status']=description_pack(r, item)
    item['decision_summary']=decision_summary(item)
    item['score']=score_default(item)
    item['variant_tags']=variant_tags(item)
    if r.get('residential_status'):
        item['residential_status']=r.get('residential_status')
        try: item['residential_reasons']=json.loads(r.get('residential_reasons_json') or '[]')
        except json.JSONDecodeError: item['residential_reasons']=[]
    else:
        status, reasons=residential_status(r)
        item['residential_status']=status
        item['residential_reasons']=reasons
    item['db_quality_score']=r.get('quality_score')
    item['db_is_canonical']=bool(r.get('is_canonical')) if r.get('is_canonical') is not None else None
    item['db_canonical_id']=f"{r.get('canonical_source_site')}:{r.get('canonical_source_id')}" if r.get('canonical_source_site') and r.get('canonical_source_id') else None
    item['db_duplicate_group_size']=r.get('duplicate_group_size')
    item['db_duplicate_group_rank']=r.get('duplicate_group_rank')
    return item

items=[]
suspects=[]
for r in rows:
    item=build_item(r)
    # filter obvious impossible monthly rents from the public app, but keep a trace
    if item['rent_eur'] and item['rent_eur']>10000:
        item['residential_status']='excluded_bad_rent'
        item['residential_reasons']=(item.get('residential_reasons') or []) + ['rent_eur>10000']
        suspects.append(item)
        continue
    if item['residential_status']=='residential_candidate':
        items.append(item)
    else:
        suspects.append(item)
def dup_norm(s):
    s=(s or '').lower()
    for ch in "àâäéèêëîïôöùûüç'-_,.;:/()[]{}":
        s=s.replace(ch,' ')
    toks=[t for t in s.split() if len(t)>2 and t not in {'appartement','maison','location','louer','pieces','pièces','saint','denis','sainte','marie','reunion','974'}]
    return ' '.join(toks[:6])

def dup_key(item):
    surface = round((item.get('surface_m2') or 0)/5)*5 if item.get('surface_m2') else 0
    rent = round((item.get('rent_eur') or 0)/50)*50 if item.get('rent_eur') else 0
    return '|'.join(map(str,[item.get('commune') or '', item.get('property_type') or '', surface, rent, item.get('rooms') or 0, dup_norm(item.get('title'))[:48]]))

all_exported=items+suspects
groups={}
for it in all_exported:
    key=dup_key(it)
    groups.setdefault(key,[]).append(it)
for key, group in groups.items():
    group_sorted=sorted(group, key=lambda x:(x.get('source_site') or '', x.get('id') or ''))
    sources=sorted({g.get('source_site') for g in group_sorted if g.get('source_site')})
    for rank,it in enumerate(group_sorted,1):
        missing=[]
        for field,label in [('rent_eur','loyer'),('surface_m2','surface'),('rooms','pièces'),('bedrooms','chambres'),('city','ville'),('url','lien source')]:
            if not it.get(field): missing.append(label)
        it['duplicate_key']=key
        it['duplicate_group_size']=len(group_sorted)
        it['duplicate_group_rank']=rank
        it['duplicate_sources']=sources
        it['similar_reasons']=(['même commune/type/surface/loyer/titre approximatif'] if len(group_sorted)>1 else [])
        it['missing_fields']=missing
        it['trust_flags']=(['données incomplètes: '+', '.join(missing)] if missing else ['données clés présentes'])

items.sort(key=lambda x:(-x['score'], x['rent_eur'] or 10**9, -(x['surface_m2'] or 0)))
suspects.sort(key=lambda x:(x['source_site'], x['rent_eur'] or 10**9, x['title']))

def load_saved_searches_summary():
    cfg_path=BASE/'config'/'saved_searches.json'
    if not cfg_path.exists():
        return {'enabled_count':0, 'items': []}
    try:
        cfg=json.loads(cfg_path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'enabled_count':0, 'items': [], 'error': str(exc)}
    searches=[s for s in cfg.get('searches',[]) if s.get('enabled', True)]
    return {
        'enabled_count': len(searches),
        'items': [
            {
                'id': s.get('id'),
                'name': s.get('name') or s.get('id'),
                'description': s.get('description',''),
                'filters': s.get('filters',{}),
            } for s in searches
        ],
    }

saved_searches_summary=load_saved_searches_summary()
changes_payload=export_changes(out_path=OUT/'changes.json')
render_changes_html(changes_payload, OUT/'changes.html')
source_health_payload=build_source_health_payload(DB)
(OUT/'source_health.json').write_text(json.dumps(source_health_payload, ensure_ascii=False, indent=2), encoding='utf-8')
render_source_health_html(source_health_payload, OUT/'source_health.html')
changes_summary=changes_payload.get('summary', {})
source_health_summary=source_health_payload.get('summary', {})
meta={
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'source_db': DB.name,
    'active_with_photo_input': len(rows),
    'residential_exported': len(items),
    'suspects_exported': len(suspects),
    'ux_research_notes': [
        'Filtres multi-valeurs + chips actifs + compteurs dynamiques, pattern Booking/LinkedIn/real-estate.',
        'Cards photo-first, prix/surface/pièces/secteur/région visibles immédiatement, source cliquable.',
        'Descriptions enrichies: texte source si disponible, sinon synthèse clairement marquée depuis les champs extraits.',
        'Variantes affichées séparément pour éviter zéro résultat strict.',
        'Modal photo plein écran avec navigation clavier et flèches.'
    ],
    'default_search': {'zones':['Sainte-Marie','Moufia','La Bretagne','Saint-Denis'], 'surface_min':60, 'rent_min':600, 'rent_max':1000, 'bedrooms_min':1, 'prefer_unfurnished': True},
    'saved_search_alerts': saved_searches_summary,
    'recent_changes': changes_summary,
    'source_health': source_health_summary,
    'db_enrichment_report': enrichment_report,
}
(OUT/'listings.json').write_text(json.dumps({'meta':meta,'listings':items}, ensure_ascii=False, indent=2), encoding='utf-8')
(OUT/'suspects.json').write_text(json.dumps({'meta':meta,'suspects':suspects}, ensure_ascii=False, indent=2), encoding='utf-8')
saved_search_admin_payload=build_saved_search_admin_payload(BASE/'config'/'saved_searches.json', OUT/'listings.json')
(OUT/'saved_searches_admin.json').write_text(json.dumps(saved_search_admin_payload, ensure_ascii=False, indent=2), encoding='utf-8')
render_saved_search_admin_html(saved_search_admin_payload, OUT/'saved_searches.html')

html_doc = r'''<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Recherche immo Réunion — moteur visuel</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400..800;1,9..40,400..800&display=swap" rel="stylesheet">
<style>
:root{--bg:#fff;--ink:#222;--muted:#6a6a6a;--line:rgba(0,0,0,.10);--soft:#f7f5f2;--soft2:#f2f2f2;--accent:#ff385c;--accent2:#e60023;--ok:#087f5b;--warn:#b25b00;--shadow:rgba(0,0,0,.02) 0 0 0 1px,rgba(0,0,0,.04) 0 2px 6px,rgba(0,0,0,.10) 0 4px 8px;--deep:rgba(0,0,0,.14) 0 22px 65px}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:'DM Sans',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif} button,input,select{font:inherit} a{color:inherit}.shell{max-width:1480px;margin:auto;padding:0 28px}.top{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.93);backdrop-filter:blur(18px);border-bottom:1px solid var(--line)}.toprow{display:flex;gap:18px;align-items:center;padding:18px 0}.brand{display:flex;align-items:center;gap:11px;min-width:245px}.mark{width:38px;height:38px;border-radius:50%;background:var(--accent);display:grid;place-items:center;color:white;font-weight:800}.brand h1{font-size:18px;margin:0;letter-spacing:-.4px}.brand p{font-size:12px;margin:0;color:var(--muted)}.searchbar{flex:1;display:flex;gap:8px;background:white;border:1px solid var(--line);box-shadow:var(--shadow);border-radius:999px;padding:8px;align-items:center}.searchbar input{border:0;outline:0;flex:1;padding:10px 14px;min-width:120px}.pillbtn,.primary{border:0;border-radius:999px;padding:10px 16px;background:var(--soft2);cursor:pointer;font-weight:700}.primary{background:var(--accent);color:white}.summary{display:flex;gap:10px;overflow:auto;padding:0 0 14px}.chip{border:1px solid var(--line);background:white;border-radius:999px;padding:9px 13px;white-space:nowrap;font-size:13px;cursor:pointer}.chip.active{background:#222;color:white;border-color:#222}.main{display:grid;grid-template-columns:322px 1fr;gap:28px;padding-top:24px}.filters{position:sticky;top:94px;align-self:start;background:white;border:1px solid var(--line);border-radius:28px;padding:18px;box-shadow:var(--shadow);max-height:calc(100vh - 116px);overflow:auto}.filters h2{font-size:20px;margin:0 0 4px;letter-spacing:-.5px}.filters .hint{font-size:13px;color:var(--muted);margin:0 0 18px}.group{border-top:1px solid var(--line);padding:16px 0}.group:first-of-type{border-top:0}.group-title{font-size:13px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);font-weight:800;margin-bottom:10px}.checks{display:grid;gap:8px}.check{display:flex;justify-content:space-between;gap:10px;align-items:center;border:1px solid var(--line);border-radius:14px;padding:10px;background:#fff;cursor:pointer}.check input{accent-color:var(--accent)}.count{font-size:12px;color:var(--muted);background:var(--soft2);border-radius:999px;padding:3px 7px}.range{display:grid;grid-template-columns:1fr 1fr;gap:8px}.range label{font-size:12px;color:var(--muted);display:grid;gap:5px}.range input,.select{width:100%;border:1px solid var(--line);border-radius:12px;padding:10px;background:white}.content{min-width:0}.hero{display:grid;grid-template-columns:1.2fr .8fr;gap:20px;align-items:stretch;margin-bottom:22px}.panel{background:var(--soft);border:1px solid var(--line);border-radius:32px;padding:24px;box-shadow:var(--shadow)}.panel h2{font-size:42px;line-height:.98;letter-spacing:-1.8px;margin:0 0 12px}.panel p{color:var(--muted);font-size:16px;line-height:1.45;margin:0}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:20px}.stat{background:white;border-radius:20px;padding:14px;border:1px solid var(--line)}.stat b{font-size:26px}.stat span{display:block;color:var(--muted);font-size:12px}.insight{background:#222;color:white;border-radius:32px;padding:24px;display:flex;flex-direction:column;justify-content:space-between;min-height:220px}.insight .small{color:rgba(255,255,255,.66);font-size:13px}.insight h3{font-size:26px;letter-spacing:-.8px;margin:8px 0}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.tab{border:1px solid var(--line);background:white;border-radius:999px;padding:10px 14px;cursor:pointer;font-weight:800}.tab.active{background:var(--ink);color:white}.section-title{display:flex;align-items:end;justify-content:space-between;gap:18px;margin:24px 0 12px}.section-title h3{font-size:25px;letter-spacing:-.6px;margin:0}.section-title p{margin:0;color:var(--muted);font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(268px,1fr));gap:22px}.card{border:0;background:white;text-align:left;cursor:pointer;border-radius:24px;overflow:hidden;box-shadow:none;transition:.18s transform,.18s box-shadow}.card:hover{transform:translateY(-2px);box-shadow:var(--shadow)}.photo{aspect-ratio:1.2/1;position:relative;background:#eee;overflow:hidden;border-radius:24px}.photo img{width:100%;height:100%;object-fit:cover;display:block}.badgebar{position:absolute;top:10px;left:10px;right:10px;display:flex;justify-content:space-between;gap:8px}.badge{background:rgba(255,255,255,.94);border-radius:999px;padding:6px 9px;font-size:12px;font-weight:800;box-shadow:0 1px 8px rgba(0,0,0,.12)}.heart{width:34px;height:34px;border-radius:50%;border:0;background:rgba(255,255,255,.88);font-weight:900}.body{padding:12px 2px 4px}.price{font-size:18px;font-weight:800;letter-spacing:-.3px}.meta{color:var(--muted);font-size:14px;line-height:1.35;margin:3px 0}.title{font-size:14px;font-weight:700;line-height:1.25;margin:6px 0;color:#333;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.tag{background:#fff1f4;color:#9f1239;border-radius:999px;padding:4px 7px;font-size:11px;font-weight:800}.empty{padding:36px;border:1px dashed var(--line);border-radius:24px;color:var(--muted);background:var(--soft)}.modal{position:fixed;inset:0;z-index:80;background:rgba(0,0,0,.82);display:none;align-items:center;justify-content:center;padding:24px}.modal.open{display:flex}.modalbox{width:min(1180px,100%);display:grid;grid-template-columns:1.2fr .8fr;gap:18px;color:white}.modalimg{background:#111;border-radius:24px;overflow:hidden;display:grid;place-items:center;min-height:70vh}.modalimg img{max-width:100%;max-height:82vh;object-fit:contain}.modalinfo{background:white;color:#222;border-radius:24px;padding:22px;align-self:start}.close,.nav{position:absolute;border:0;border-radius:50%;background:white;color:#222;width:44px;height:44px;font-size:22px;cursor:pointer}.close{right:22px;top:22px}.nav.prev{left:22px}.nav.next{right:22px}.modalinfo h3{font-size:26px;line-height:1.05;margin:0 0 8px}.modalinfo .desc{color:#555;line-height:1.45;max-height:190px;overflow:auto}.source{display:inline-flex;margin-top:14px;background:#222;color:white;border-radius:999px;padding:10px 13px;text-decoration:none;font-weight:800}.footer{margin:36px 0 20px;padding:18px;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}@media(max-width:980px){.main{grid-template-columns:1fr}.filters{position:relative;top:0;max-height:none}.hero{grid-template-columns:1fr}.toprow{flex-wrap:wrap}.brand{min-width:0}.modalbox{grid-template-columns:1fr}.modalimg{min-height:45vh}}@media(max-width:560px){.shell{padding:0 14px}.grid{grid-template-columns:1fr}.panel h2{font-size:32px}.stats{grid-template-columns:1fr}.searchbar{border-radius:24px;flex-wrap:wrap}.searchbar input{flex-basis:100%}}

.card .locline{font-size:13px;color:var(--muted);font-weight:700;margin-top:6px}.card .why{font-size:12px;color:var(--ok);font-weight:800;margin-top:7px}.card .descmini{font-size:13px;color:#4b4b4b;line-height:1.35;margin-top:9px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.descstatus{display:inline-block;margin-top:10px;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:12px;color:var(--muted);background:var(--soft)}
.cardMain{display:grid;grid-template-columns:210px 1fr;gap:13px;width:100%;border:0;background:transparent;text-align:left;padding:0;color:inherit;cursor:pointer}.cardActions,.modalActions{display:flex;gap:7px;flex-wrap:wrap;padding:0 14px 14px 14px}.modalActions{padding:8px 0 12px 0}.act{border:1px solid var(--line);border-radius:999px;background:#fff;padding:8px 10px;font-weight:800;font-size:12px;text-decoration:none;cursor:pointer;min-height:36px}.act:hover,.pillbtn:hover{border-color:#222}.savedCard{box-shadow:inset 0 0 0 2px rgba(255,56,92,.35),var(--shadow)}.suspectCard{background:#fffaf3}.sourceMini{display:inline-flex;align-items:center}.card.card{display:block;overflow:hidden}.card .body{padding:14px 14px 10px 0}

.mobileActions,.mobileOnly{display:none}.filterHead{display:block}.scrim{display:none}.photo.noimg{display:grid;place-items:center}.photo.noimg:after{content:'Photo indisponible';color:#777;font-weight:800;font-size:13px}.card{content-visibility:auto;contain-intrinsic-size:420px}.card .locline{font-size:13px;color:var(--muted);font-weight:700;margin-top:6px}.card .why{font-size:12px;color:var(--ok);font-weight:800;margin-top:7px}.card .descmini{font-size:13px;color:#4b4b4b;line-height:1.35;margin-top:9px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.descstatus{display:inline-block;margin-top:10px;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:12px;color:var(--muted);background:var(--soft)}
.cardMain{display:grid;grid-template-columns:210px 1fr;gap:13px;width:100%;border:0;background:transparent;text-align:left;padding:0;color:inherit;cursor:pointer}.cardActions,.modalActions{display:flex;gap:7px;flex-wrap:wrap;padding:0 14px 14px 14px}.modalActions{padding:8px 0 12px 0}.act{border:1px solid var(--line);border-radius:999px;background:#fff;padding:8px 10px;font-weight:800;font-size:12px;text-decoration:none;cursor:pointer;min-height:36px}.act:hover,.pillbtn:hover{border-color:#222}.savedCard{box-shadow:inset 0 0 0 2px rgba(255,56,92,.35),var(--shadow)}.suspectCard{background:#fffaf3}.sourceMini{display:inline-flex;align-items:center}.card.card{display:block;overflow:hidden}.card .body{padding:14px 14px 10px 0}
@media (max-width: 760px){
 body{font-size:15px}.shell{padding:0 14px}.top{position:sticky;top:0}.toprow{display:grid;gap:10px;padding:10px 0}.brand{min-width:0}.brand p{display:none}.mark{width:32px;height:32px}.searchbar{border-radius:18px;padding:6px}.searchbar input{padding:9px 10px}.desktopOnly{display:none!important}.mobileOnly,.mobileActions{display:flex}.mobileActions{gap:8px;overflow:auto;padding:0 0 10px}.mobileActions .primary,.mobileActions .pillbtn{white-space:nowrap;padding:9px 12px}.summary{display:flex;padding:0 0 8px;gap:7px}.chip{padding:8px 11px;font-size:12px;min-height:38px}.main{display:block;padding-top:12px}.content{display:block}.hero{display:block;margin-bottom:10px}.panel{border-radius:20px;padding:14px}.panel h2{font-size:26px;line-height:1.04;margin-bottom:8px}.panel p{font-size:13px}.stats{grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:12px}.stat{padding:9px;border-radius:14px}.stat b{font-size:20px}.stat span{font-size:10px}.insight{display:none}.tabs{flex-wrap:nowrap;overflow:auto;margin:10px 0;padding-bottom:4px}.tab{white-space:nowrap;padding:8px 10px;font-size:12px}.section-title{align-items:start;margin:12px 0;display:grid;grid-template-columns:1fr;gap:8px}.section-title h3{font-size:22px}.select{max-width:none!important}.grid{display:grid;grid-template-columns:1fr;gap:12px}.card{display:grid;grid-template-columns:108px 1fr;gap:10px;border:1px solid var(--line);border-radius:18px;padding:8px;min-height:160px;box-shadow:var(--shadow)}.photo{width:108px;height:132px;aspect-ratio:auto;border-radius:14px}.badgebar{top:6px;left:6px;right:6px;display:block}.badge{display:inline-block;margin:0 3px 4px 0;font-size:10px;padding:4px 6px}.body{padding:0}.price{font-size:17px}.meta{font-size:12px}.title{font-size:13px;-webkit-line-clamp:2}.card .locline{font-size:12px}.card .why{font-size:11px}.card .descmini{font-size:12px;-webkit-line-clamp:2;margin-top:6px}.descstatus{font-size:10px;padding:3px 6px;margin-top:6px}.tags{display:none}.filters{position:fixed;z-index:70;left:0;right:0;bottom:0;top:auto;max-height:82vh;transform:translateY(105%);transition:transform .2s ease;border-radius:22px 22px 0 0;padding:14px;overflow:auto}.filtersOpen .filters{transform:translateY(0)}.filterHead{display:flex;align-items:start;justify-content:space-between;gap:10px;position:sticky;top:-14px;background:white;z-index:2;padding:10px 0;border-bottom:1px solid var(--line)}.filters .hint{margin-bottom:8px}.group{padding:11px 0}.checks{grid-template-columns:1fr 1fr;gap:6px}.check{padding:8px;border-radius:12px;font-size:12px}.count{font-size:10px}.range{grid-template-columns:1fr 1fr}.scrim{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:60;display:none}.filtersOpen .scrim{display:block}.modal{padding:0}.modalbox{height:100%;grid-template-columns:1fr;gap:0;overflow:auto;background:#111}.modalimg{border-radius:0;max-height:45vh}.modalinfo{border-radius:0;padding:16px}.nav{display:none}.close{top:10px;right:10px;z-index:90}.footer{font-size:12px;padding-bottom:30px}
}


@media (max-width: 760px){
 .toprow{padding:8px 0}.header,.top{max-height:none}.mobileActions{padding-bottom:6px}.summary{display:flex}.hero{margin-bottom:6px}.panel{padding:12px}.panel h2{font-size:22px}.panel p{display:none}.stats{margin-top:6px}.stat{padding:7px}.tabs{margin:6px 0}.section-title{margin:8px 0}.grid{gap:8px}.card{grid-template-columns:82px 1fr;gap:8px;min-height:118px;height:auto;padding:7px;border-radius:15px;content-visibility:visible;contain-intrinsic-size:auto}.photo{width:82px;height:102px;border-radius:12px}.badgebar{display:none}.price{font-size:16px}.meta{font-size:11px;margin:1px 0}.title{font-size:12px;margin:3px 0;-webkit-line-clamp:1}.card .locline{font-size:11px;margin-top:3px}.card .why{font-size:10px;margin-top:3px}.card .descmini{font-size:11px;line-height:1.25;-webkit-line-clamp:2;margin-top:3px}.descstatus{display:none}.filters{max-height:78vh}.checks{grid-template-columns:1fr}.modalinfo .desc{white-space:pre-wrap}.footer{display:block;font-size:12px;padding:18px 0 34px}
}


@media (max-width:760px){body.filtersOpen .filters{transform:translateY(0)!important;top:auto!important;bottom:0!important;left:0!important;right:0!important;position:fixed!important}body.filtersOpen .scrim{display:block!important}}


@media (max-width:760px){.filters{display:none!important;transform:none!important;top:auto!important;bottom:0!important;left:0!important;right:0!important;position:fixed!important}body.filtersOpen .filters{display:block!important;transform:none!important}body.filtersOpen .scrim{display:block!important}}


.galleryBadge{position:absolute;right:8px;bottom:8px;background:rgba(0,0,0,.72);color:#fff;border-radius:999px;padding:5px 8px;font-size:12px;font-weight:800;backdrop-filter:blur(8px)}
.modalimg{position:relative}.photoCounter{position:absolute;left:14px;bottom:14px;background:rgba(0,0,0,.70);color:#fff;border-radius:999px;padding:7px 10px;font-size:13px;font-weight:800}.modalPhotoNav{position:absolute;top:50%;transform:translateY(-50%);border:0;border-radius:999px;width:42px;height:42px;background:rgba(255,255,255,.88);box-shadow:var(--shadow);font-size:28px;font-weight:800;cursor:pointer}.modalPhotoNav.left{left:12px}.modalPhotoNav.right{right:12px}.modalPhotoNav[hidden],.photoCounter[hidden]{display:none!important}
@media (max-width:760px){.galleryBadge{right:5px;bottom:5px;font-size:10px;padding:4px 6px}.modalPhotoNav{width:36px;height:36px;font-size:22px}.photoCounter{font-size:12px;left:10px;bottom:10px}}

@media (max-width:760px){.card.card{display:block;min-height:0}.cardMain{grid-template-columns:108px 1fr;gap:10px}.cardActions{padding:8px 0 2px 0;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:5px}.cardActions .act{font-size:11px;padding:7px 5px;text-align:center;justify-content:center}.sourceMini{display:flex}.card .body{padding:0}.mobileActions{flex-wrap:wrap;overflow:visible}.tabs{flex-wrap:wrap;overflow:visible}.mobileActions .pillbtn,.mobileActions .primary{min-height:39px}.modalActions .pillbtn,.modalActions .primary{min-height:40px}}
.mapPanel{background:#fff;border:1px solid var(--line);border-radius:28px;padding:16px;margin:14px 0 18px;box-shadow:var(--shadow)}.mapPanel h3{margin:0 0 4px;font-size:22px;letter-spacing:-.5px}.mapPanel p{margin:0 0 12px;color:var(--muted);font-size:13px}.zoneMap{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.zoneTile{border:1px solid var(--line);background:var(--soft);border-radius:18px;padding:12px;text-align:left;cursor:pointer;min-height:92px}.zoneTile b{display:block;font-size:24px}.zoneTile span{display:block;color:var(--muted);font-size:12px}.zoneTile.active{background:#222;color:#fff}.zoneTile.active span{color:#eee}.adminLinks{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.healthDot{display:inline-block;width:8px;height:8px;border-radius:999px;background:var(--ok);margin-right:6px}.healthDot.warn{background:var(--warn)}.healthDot.bad{background:var(--accent2)}
@media (max-width:760px){.mapPanel{border-radius:20px;padding:12px;margin:8px 0}.zoneMap{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.zoneTile{min-height:72px;padding:10px}.zoneTile b{font-size:19px}.adminLinks a{min-height:40px;display:inline-flex;align-items:center}}
</style>
</head>
<body>
<header class="top"><div class="shell"><div class="toprow"><div class="brand"><div class="mark">974</div><div><h1>Recherche immo RUN</h1><p>Base locale · annonces actives avec photos</p></div></div><div class="searchbar"><input id="q" placeholder="Chercher: Moufia, T3, jardin…"><button class="primary desktopOnly" id="resetDefault">Recherche exemple</button><button class="pillbtn desktopOnly" id="copySearch">Copier recherche</button><button class="pillbtn desktopOnly" id="clearAll">Tout effacer</button></div></div><div class="mobileActions"><button class="primary" id="openFilters">Filtres <span id="mobileCount">0</span></button><button class="pillbtn" id="showAllMobile">Tout</button><button class="pillbtn" id="showSavedMobile">★ 0</button><button class="pillbtn" id="showHiddenMobile">Masqués 0</button><button class="pillbtn" id="showSuspectsMobile">Suspects 68</button><button class="pillbtn" id="copySearchMobile">Copier recherche</button><button class="pillbtn" id="clearAllMobile">Tout effacer</button><button class="pillbtn" id="resetDefaultMobile">Exemple</button></div><div class="summary" id="quickChips"></div></div></header>
<main class="shell main"><section class="content"><div class="hero"><div class="panel"><h2>Vue immo résidentielle / non commerciale</h2><p><span id="heroCount">Base active filtrée</span>. Par défaut, les biens non commerciaux passent en premier; les locaux/bureaux/box/terrains suspects sont exclus de cette vue. Les filtres sont testés contre l’export JSON issu de la base.</p><div class="stats"><div class="stat"><b id="visibleCount">0</b><span>visibles maintenant</span></div><div class="stat"><b id="strictCount">0</b><span>matchs stricts</span></div><div class="stat"><b id="variantCount">0</b><span>autres variantes</span></div></div></div><div class="insight"><div><div class="small">Lecture rapide</div><h3>Prix · surface · secteur · raison</h3><div class="small">Photo si disponible, description source ou synthèse factuelle. Pas besoin de faire défiler les photos.</div><div class="small" id="alertSummary"></div></div><button class="primary" id="scrollResults">Aller aux résultats</button></div></div><section class="mapPanel" aria-label="Carte légère par région"><h3>Carte rapide par secteur</h3><p>Filtre visuel par région. Les compteurs suivent les annonces canoniques non masquées.</p><div class="zoneMap" id="zoneMap"></div><div class="adminLinks"><a href="/source_health.html" target="_blank" rel="noopener"><span class="healthDot" id="healthDot"></span>Santé sources</a><a href="/saved_searches.html" target="_blank" rel="noopener">Cockpit alertes</a><a href="/changes.html" target="_blank" rel="noopener">Journal changements</a></div></section><div class="tabs" id="tabs"></div><div class="section-title"><div><h3 id="sectionHeading">Résultats</h3><p id="sectionHint">La card explique pourquoi elle ressort.</p></div><select id="sort" class="select" style="max-width:230px"><option value="score">Tri recommandé</option><option value="priceAsc">Prix croissant</option><option value="surfaceDesc">Surface décroissante</option><option value="recent">Vu récemment</option></select></div><div id="cards" class="grid"></div><div class="footer">Données générées depuis la base locale. Les zones/adresses dépendent de ce que les sources fournissent; Moufia/La Bretagne/Sainte-Marie sont détectées depuis ville+titre+description+URL. <a id="suspectsLink" href="/suspects.json" target="_blank" rel="noopener">Voir les suspects exclus</a>.</div></section><aside class="filters" id="filtersPanel"><div class="filterHead"><div><h2>Filtres</h2><p class="hint">Compte recalculé avec les filtres actifs.</p></div><button class="pillbtn mobileOnly" id="closeFilters">Fermer</button></div><div id="filterRoot"></div></aside><div class="scrim" id="scrim"></div></main>
<div class="modal" id="modal"><button class="close" id="closeModal">×</button><button class="nav prev" id="prev">‹</button><div class="modalbox"><div class="modalimg"><img id="modalPhoto" alt="Photo annonce"><button class="modalPhotoNav left" id="photoPrev" type="button">‹</button><button class="modalPhotoNav right" id="photoNext" type="button">›</button><div class="photoCounter" id="photoCounter"></div></div><div class="modalinfo"><div class="price" id="modalPrice"></div><h3 id="modalTitle"></h3><div class="meta" id="modalMeta"></div><div class="tags" id="modalTags"></div><p class="desc" id="modalDesc"></p><div class="modalActions" id="modalActions"></div><a class="source" id="modalLink" target="_blank" rel="noopener">Ouvrir l’annonce source</a></div></div><button class="nav next" id="next">›</button></div>
<script id="embeddedData" type="application/json">__DATA__</script>
<script>
const DATA=JSON.parse(document.getElementById('embeddedData').textContent); const listings=DATA.listings; const suspects=DATA.suspects||[];
const canonicalListings=listings.filter(x=>x.db_is_canonical!==false);
const similarListings=listings.filter(x=>(x.duplicate_group_size||1)>1 || x.db_is_canonical===false);
const primaryListings=()=>canonicalListings.filter(x=>!hidden.has(x.id));
const heroCountEl=document.getElementById('heroCount');
if(heroCountEl){heroCountEl.textContent=`${canonicalListings.length} annonces canoniques sur ${DATA.meta?.residential_exported||listings.length} résidentielles avec photo · ${similarListings.length} similaires en onglet séparé`;}
const alertSummaryEl=document.getElementById('alertSummary');
if(alertSummaryEl){const n=DATA.meta?.saved_search_alerts?.enabled_count||0; const ch=DATA.meta?.recent_changes||{}; const bits=[]; if(n) bits.push(`Alertes prêtes: ${n} recherche${n>1?'s':''} surveillée${n>1?'s':''}`); if((ch.total_events||0)>0) bits.push(`changements récents: ${ch.total_events}, dont ${ch.price_drops||0} baisse${(ch.price_drops||0)>1?'s':''}`); bits.push('<a href="/changes.html" target="_blank" rel="noopener">journal changements</a>'); alertSummaryEl.innerHTML=bits.join(' · ');}
const blankState={region:[],zones:[],commune:[],property_type:[],furnished:[],source_site:[],rentMin:null,rentMax:null,surfaceMin:null,roomsMin:null,bedroomsMin:null,minScore:null,q:'',tab:'all',sort:'score'};
const defaultState=structuredClone(blankState);
const exampleState={region:['Nord'], zones:['Sainte-Marie','Moufia','La Bretagne','Saint-Denis'], commune:[], property_type:['Appartement','Maison','Appartement / maison'], furnished:['Non meublé','Non précisé'], source_site:[], rentMin:600, rentMax:1000, surfaceMin:60, roomsMin:2, bedroomsMin:1, minScore:null, q:'', tab:'all', sort:'score'};
const LS_STATE='immoSearchState', LS_SAVED='immoSavedIds', LS_HIDDEN='immoHiddenIds';
const ARRAY_URL_KEYS={r:'region',z:'zones',c:'commune',t:'property_type',f:'furnished',src:'source_site'};
const NUM_URL_KEYS={rentMin:'rentMin',rentMax:'rentMax',surfaceMin:'surfaceMin',roomsMin:'roomsMin',bedroomsMin:'bedroomsMin',minScore:'minScore'};
function splitParam(v){return (v||'').split('|').map(x=>x.trim()).filter(Boolean)}
function stateFromUrl(){const p=new URLSearchParams(location.search); if(p.get('immo')!=='1') return null; const st=structuredClone(blankState); Object.entries(ARRAY_URL_KEYS).forEach(([u,k])=>{st[k]=splitParam(p.get(u)||'')}); Object.entries(NUM_URL_KEYS).forEach(([u,k])=>{const v=p.get(u); st[k]=v?Number(v):null}); st.q=p.get('q')||''; st.tab=p.get('tab')||'all'; st.sort=p.get('sort')||'score'; return st;}
function encodeStateToParams(st){const p=new URLSearchParams(); p.set('immo','1'); Object.entries(ARRAY_URL_KEYS).forEach(([u,k])=>{if((st[k]||[]).length)p.set(u,st[k].join('|'))}); Object.entries(NUM_URL_KEYS).forEach(([u,k])=>{if(st[k]!=null&&st[k]!==''&&!Number.isNaN(st[k]))p.set(u,String(st[k]))}); if(st.q)p.set('q',st.q); if(st.tab&&st.tab!=='all')p.set('tab',st.tab); if(st.sort&&st.sort!=='score')p.set('sort',st.sort); return p;}
function syncUrlState(){const url=new URL(location.href); const keep=new URLSearchParams(url.search); ['immo','r','z','c','t','f','src','rentMin','rentMax','surfaceMin','roomsMin','bedroomsMin','minScore','q','tab','sort','reset'].forEach(k=>keep.delete(k)); const enc=encodeStateToParams(state); enc.forEach((v,k)=>keep.set(k,v)); url.search=keep.toString(); history.replaceState({immoState:state},'',url);}
if(new URLSearchParams(location.search).get('reset')==='1'){localStorage.removeItem(LS_STATE);}
let state=stateFromUrl()||JSON.parse(localStorage.getItem(LS_STATE)||'null')||structuredClone(defaultState); let current=[]; let modalIndex=0; let modalPhotoIndex=0;
let saved=new Set(JSON.parse(localStorage.getItem(LS_SAVED)||'[]')); let hidden=new Set(JSON.parse(localStorage.getItem(LS_HIDDEN)||'[]'));
const $=s=>document.querySelector(s); const $$=s=>[...document.querySelectorAll(s)];
const fmtEuro=v=>v?new Intl.NumberFormat('fr-FR').format(v)+' €/mois':'Prix n.c.'; const fmtM=v=>v?Math.round(v)+' m²':'Surface n.c.';
function uniq(arr){return [...new Set(arr.filter(Boolean))]} function norm(s){return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]+/g,' ').trim().replace(/\s+/g,' ')}
function searchable(x){return norm([x.id,x.source_id,x.title,x.city,x.region,x.commune,x.location_label,x.primary_zone,(x.zones||[]).join(' '),x.description,x.decision_summary,x.source_site,x.url,(x.residential_reasons||[]).join(' '),(x.trust_flags||[]).join(' '),(x.variant_tags||[]).join(' ')].join(' '))}
function opportunityScore(x){return Number((x.opportunity_analysis||{}).score ?? x.opportunity_score ?? x.score ?? 0)}
function persistLists(){localStorage.setItem(LS_SAVED,JSON.stringify([...saved])); localStorage.setItem(LS_HIDDEN,JSON.stringify([...hidden])); updateActionCounts();}
function allValues(key){return uniq(canonicalListings.flatMap(x=>Array.isArray(x[key])?x[key]:[x[key]])).sort((a,b)=>String(a).localeCompare(String(b),'fr'))}
const filters=[['region','Régions', ['Nord','Est','Ouest','Sud','Région non précisée']], ['zones','Zones prioritaires', ['Sainte-Marie','Moufia','La Bretagne','Saint-Denis']], ['commune','Communes', allValues('commune').filter(x=>x!=='Non précisée').slice(0,20)], ['property_type','Type', ['Appartement','Maison','Appartement / maison']], ['furnished','Meublé', ['Non meublé','Non précisé','Meublé']], ['source_site','Sources', allValues('source_site')]];
function matches(item, st=state, ignore=null){
 const text=searchable(item); if(ignore!=='q' && st.q && !text.includes(norm(st.q))) return false;
 if(ignore!=='region' && st.region?.length && !st.region.includes(item.region)) return false;
 if(ignore!=='zones' && st.zones?.length && !st.zones.some(z=>item.zones?.includes(z)||item.primary_zone===z)) return false;
 if(ignore!=='commune' && st.commune?.length && !st.commune.includes(item.commune)) return false;
 if(ignore!=='property_type' && st.property_type?.length && !st.property_type.includes(item.property_type)) return false;
 if(ignore!=='furnished' && st.furnished?.length && !st.furnished.includes(item.furnished)) return false;
 if(ignore!=='source_site' && st.source_site?.length && !st.source_site.includes(item.source_site)) return false;
 if(ignore!=='rent' && st.rentMin && (!item.rent_eur || item.rent_eur<st.rentMin)) return false;
 if(ignore!=='rent' && st.rentMax && (!item.rent_eur || item.rent_eur>st.rentMax)) return false;
 if(ignore!=='surface' && st.surfaceMin && (!item.surface_m2 || item.surface_m2<st.surfaceMin)) return false;
 if(ignore!=='rooms' && st.roomsMin && (!item.rooms || item.rooms<st.roomsMin)) return false;
 if(ignore!=='bedrooms' && st.bedroomsMin && (!item.bedrooms || item.bedrooms<st.bedroomsMin)) return false;
 if(ignore!=='score' && st.minScore && opportunityScore(item)<st.minScore) return false;
 return true;
}
function strictExample(item){return ['Sainte-Marie','Moufia','La Bretagne','Saint-Denis'].some(z=>item.zones?.includes(z)||item.primary_zone===z) && item.surface_m2>=60 && item.rent_eur>=600 && item.rent_eur<=1000 && ((item.bedrooms||0)>=1 || (item.rooms||0)>=2)}
function tabMatch(item){ if(state.tab==='strict') return strictExample(item); if(state.tab==='target') return ['Sainte-Marie','Moufia','La Bretagne','Saint-Denis'].some(z=>item.zones?.includes(z)||item.primary_zone===z); if(state.tab==='sameBudgetElsewhere') return !['Sainte-Marie','Moufia','La Bretagne','Saint-Denis'].some(z=>item.zones?.includes(z)||item.primary_zone===z) && item.surface_m2>=60 && item.rent_eur>=600 && item.rent_eur<=1000; if(state.tab==='furnished') return item.furnished==='Meublé'; if(state.tab==='saved') return saved.has(item.id); if(state.tab==='duplicates') return (item.duplicate_group_size||1)>1; return true; }
function currentBase(){ if(state.tab==='suspects') return suspects; if(state.tab==='hidden') return listings.concat(suspects).filter(x=>hidden.has(x.id)); if(state.tab==='duplicates') return similarListings.filter(x=>!hidden.has(x.id)); return primaryListings(); }
function filtered(){let base=currentBase(); let arr; if(['suspects','hidden','saved','duplicates'].includes(state.tab)){arr=base.filter(x=>!state.q||searchable(x).includes(norm(state.q))); if(state.tab==='saved') arr=arr.filter(x=>saved.has(x.id)); if(state.tab==='duplicates') arr=arr.filter(x=>(x.duplicate_group_size||1)>1 || x.db_is_canonical===false);} else {arr=base.filter(x=>matches(x)&&tabMatch(x));} const sort=$('#sort')?.value||state.sort; arr.sort((a,b)=> sort==='priceAsc'?(a.rent_eur||1e9)-(b.rent_eur||1e9):sort==='surfaceDesc'?(b.surface_m2||0)-(a.surface_m2||0):sort==='recent'?String(b.seen_last_at||'').localeCompare(String(a.seen_last_at||'')):(b.score-a.score)); return arr;}
function countFor(key,val){let st={...state}; if(['region','zones','commune','property_type','furnished','source_site'].includes(key)){let a=[...(st[key]||[])]; a.includes(val)?a=a.filter(x=>x!==val):a.push(val); st[key]=a;} return primaryListings().filter(x=>matches(x,st)).length;}
function renderZoneMap(){const root=$('#zoneMap'); if(!root)return; const regions=['Nord','Sud','Ouest','Est','Région non précisée']; const counts=Object.fromEntries(regions.map(r=>[r,primaryListings().filter(x=>x.region===r).length])); root.innerHTML=regions.map(r=>`<button class="zoneTile ${(state.region||[]).includes(r)?'active':''}" data-region="${escapeAttr(r)}"><b>${counts[r]||0}</b><span>${escapeHtml(r)}</span></button>`).join(''); root.querySelectorAll('.zoneTile').forEach(b=>b.onclick=()=>{const r=b.dataset.region; const already=(state.region||[]).includes(r); state.region=already?[]:[r]; state.zones=[]; state.commune=[]; state.tab='all'; saveRender(); $('#cards')?.scrollIntoView({behavior:'smooth', block:'start'});}); const dot=$('#healthDot'); if(dot){const sev=DATA.meta?.source_health?.severity_counts||{}; dot.className='healthDot '+((sev.high||0)?'bad':((sev.warning||0)?'warn':''));}}
function renderFilters(){const root=$('#filterRoot'); let html=''; filters.forEach(([key,title,vals])=>{html+=`<div class="group"><div class="group-title">${title}</div><div class="checks">`; vals.forEach(v=>{const selected=(state[key]||[]).includes(v); html+=`<label class="check"><span><input type="checkbox" data-key="${key}" value="${escapeAttr(v)}" ${selected?'checked':''}> ${escapeHtml(v)}</span><span class="count">${countFor(key,v)}</span></label>`}); html+='</div></div>'}); html+=`<div class="group"><div class="group-title">Loyer mensuel</div><div class="range"><label>Min<input id="rentMin" type="number" value="${state.rentMin||''}" placeholder="600"></label><label>Max<input id="rentMax" type="number" value="${state.rentMax||''}" placeholder="1000"></label></div></div><div class="group"><div class="group-title">Surface / pièces</div><div class="range"><label>Surface min<input id="surfaceMin" type="number" value="${state.surfaceMin||''}" placeholder="60"></label><label>Pièces min<input id="roomsMin" type="number" value="${state.roomsMin||''}" placeholder="2"></label><label>Chambres min<input id="bedroomsMin" type="number" value="${state.bedroomsMin||''}" placeholder="1"></label></div></div>`; root.innerHTML=html; root.querySelectorAll('input[type=checkbox]').forEach(i=>i.onchange=e=>{const k=e.target.dataset.key,v=e.target.value; state[k]=state[k]||[]; state[k]=e.target.checked?uniq([...state[k],v]):state[k].filter(x=>x!==v); saveRender();}); ['rentMin','rentMax','surfaceMin','roomsMin','bedroomsMin'].forEach(id=>{const el=$('#'+id); if(el) el.oninput=()=>{state[id]=el.value?Number(el.value):null; saveRender();}})}
function renderChips(){const dupCount=similarListings.filter(x=>!hidden.has(x.id)).length; const chips=[['all','Canonique'],['strict','Match strict'],['target','Zones ciblées'],['sameBudgetElsewhere','Même budget/surface ailleurs'],['furnished','Meublées'],['duplicates','Similaires'],['saved','★ Sauvegardées'],['hidden','Masquées'],['suspects','Suspects exclus']]; $('#tabs').innerHTML=chips.map(([k,l])=>{const n=k==='suspects'?suspects.length:k==='saved'?[...saved].filter(id=>listings.concat(suspects).some(x=>x.id===id)).length:k==='hidden'?hidden.size:k==='duplicates'?dupCount:primaryListings().filter(x=>{let old=state.tab; state.tab=k; let m=tabMatch(x); state.tab=old; return m}).length; return `<button class="tab ${state.tab===k?'active':''}" data-tab="${k}">${l} <span class="count">${n}</span></button>`}).join(''); $$('.tab').forEach(b=>b.onclick=()=>{state.tab=b.dataset.tab; saveRender()}); const quick=['Sainte-Marie','Moufia','La Bretagne','Saint-Denis','Meublé','Non meublé','≥ 60 m²','600–1000 €']; $('#quickChips').innerHTML=quick.map(c=>`<button class="chip" data-chip="${c}">${c}</button>`).join(''); $$('.chip').forEach(b=>b.onclick=()=>quickToggle(b.dataset.chip)); updateActionCounts();}
function quickToggle(c){ if(['Sainte-Marie','Moufia','La Bretagne','Saint-Denis'].includes(c)){state.zones=state.zones||[]; state.zones=state.zones.includes(c)?state.zones.filter(x=>x!==c):[...state.zones,c];} if(c==='Meublé'||c==='Non meublé'){state.furnished=state.furnished||[]; state.furnished=state.furnished.includes(c)?state.furnished.filter(x=>x!==c):[...state.furnished,c];} if(c==='≥ 60 m²') state.surfaceMin=state.surfaceMin?null:60; if(c==='600–1000 €'){state.rentMin=state.rentMin?null:600; state.rentMax=state.rentMax?null:1000;} saveRender();}
function renderCards(){current=filtered(); $('#visibleCount').textContent=current.length; const mc=$('#mobileCount'); if(mc) mc.textContent=current.length; $('#strictCount').textContent=primaryListings().filter(x=>strictExample(x)).length; $('#variantCount').textContent= state.tab==='suspects'?suspects.length:state.tab==='saved'?saved.size:state.tab==='hidden'?hidden.size:primaryListings().filter(x=>!strictExample(x)&&x.variant_tags?.length).length; $('#sectionHeading').textContent= state.tab==='suspects'?'Suspects exclus de la vue principale':state.tab==='hidden'?'Annonces masquées — restaurables':state.tab==='saved'?'Shortlist sauvegardée':state.tab==='duplicates'?'Annonces similaires / non-canoniques':state.tab==='strict'?'Match strict de ta recherche':state.tab==='target'?'Toutes les zones ciblées':state.tab==='sameBudgetElsewhere'?'Même budget/surface ailleurs':state.tab==='furnished'?'Variantes meublées':'Résultats'; $('#sectionHint').textContent=state.tab==='hidden'?'Tu peux restaurer une annonce masquée.':state.tab==='saved'?'Ta shortlist est stockée localement dans ce navigateur.':state.tab==='duplicates'?'Groupes prudents: la vue principale ne montre que le canonique; ici tu vois les alternatives sources/non-canoniques.':state.tab==='suspects'?'Locaux/bureaux/parkings/box/terrains ou loyers impossibles isolés avec raison.':'La card explique pourquoi elle ressort.'; $('#cards').innerHTML=current.length?current.map((it,i)=>card(it,i)).join(''):`<div class="empty">Aucune annonce pour ces filtres. Essaie d’élargir le budget, la surface ou de regarder les variantes.</div>`; $$('.cardMain').forEach(b=>b.onclick=()=>openModal(Number(b.dataset.i))); $$('.act').forEach(b=>b.onclick=e=>{e.preventDefault(); e.stopPropagation(); handleAction(b.dataset.act, current[Number(b.dataset.i)]);}); updateActionCounts();}
function gallery(it){return (it.local_image_urls&&it.local_image_urls.length?it.local_image_urls:(it.local_image_url?[it.local_image_url]:(it.image_url?[it.image_url]:[]))).filter(Boolean)}
function statusBadges(it){let out=[]; if((it.duplicate_group_size||1)>1) out.push(`≈ ${it.duplicate_group_size} similaires`); if((it.missing_fields||[]).length) out.push(`Champs manquants: ${it.missing_fields.slice(0,3).join(', ')}`); else out.push('Données clés OK'); return out;}
function card(it,i){const imgs=gallery(it); const imgSrc=imgs[0]||it.image_url; const more=imgs.length>1?`<span class="galleryBadge">+${imgs.length-1} photos</span>`:''; const suspect=it.residential_status&&it.residential_status!=='residential_candidate'; const reasons=(it.residential_reasons||[]).slice(0,2).join(' · '); const isSaved=saved.has(it.id), isHidden=hidden.has(it.id); return `<article class="card ${suspect?'suspectCard':''} ${isSaved?'savedCard':''}"><button class="cardMain" data-i="${i}"><div class="photo"><img loading="eager" decoding="async" onerror="this.closest('.photo').classList.add('noimg'); this.remove()" src="${escapeAttr(imgSrc)}" alt="${escapeAttr(it.title)}">${more}<div class="badgebar"><span class="badge">${escapeHtml(it.region||'Région n.c.')}</span><span class="badge">${escapeHtml(it.source_site)}</span></div></div><div class="body"><div class="price">${fmtEuro(it.rent_eur)}</div><div class="meta">${fmtM(it.surface_m2)} · ${it.rooms||'?'} pièces · ${it.bedrooms||'?'} ch. · ${escapeHtml(it.furnished||'')}</div><div class="title">${escapeHtml(it.title)}</div><div class="locline">${escapeHtml(it.location_label||it.commune||'Secteur n.c.')} · Vu ${escapeHtml((it.seen_last_at||'date n.c.').slice(0,10))}</div><div class="why">${escapeHtml(suspect?('Exclu: '+reasons):(it.decision_summary||''))}</div><div class="descmini">${escapeHtml(it.description||'')}</div><span class="descstatus">${escapeHtml(suspect?(it.residential_status||'Suspect exclu'):(it.description_status||''))}</span><div class="tags">${[...statusBadges(it),...(suspect?(it.residential_reasons||[]):(it.variant_tags||[]))].slice(0,5).map(t=>`<span class="tag">${escapeHtml(t)}</span>`).join('')}</div></div></button><div class="cardActions"><button class="act" data-act="save" data-i="${i}">${isSaved?'★ Sauvée':'☆ Sauver'}</button><button class="act" data-act="hide" data-i="${i}">${isHidden?'Restaurer':'Masquer'}</button><button class="act" data-act="copy" data-i="${i}">Copier</button><button class="act" data-act="share" data-i="${i}">Partager</button><a class="act sourceMini" href="${escapeAttr(it.url||'#')}" target="_blank" rel="noopener">Source</a></div></article>`}
function setModalPhoto(delta=0){const it=current[modalIndex]; const imgs=gallery(it); if(!imgs.length)return; modalPhotoIndex=(modalPhotoIndex+delta+imgs.length)%imgs.length; $('#modalPhoto').src=imgs[modalPhotoIndex]; $('#photoCounter').textContent=`${modalPhotoIndex+1}/${imgs.length}`; const multi=imgs.length>1; $('#photoPrev').hidden=!multi; $('#photoNext').hidden=!multi; $('#photoCounter').hidden=!multi;}
function shareText(it){return `${it.title}\n${fmtEuro(it.rent_eur)} · ${fmtM(it.surface_m2)} · ${it.location_label||it.commune||''}\nSource: ${it.url||''}`}
async function copyShare(it, native=false){const text=shareText(it); if(native&&navigator.share){try{await navigator.share({title:it.title,text,url:it.url}); return;}catch(e){}} try{await navigator.clipboard.writeText(text);}catch(e){const ta=document.createElement('textarea'); ta.value=text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();} alert('Annonce copiée dans le presse-papiers');}
function handleAction(act,it){ if(!it)return; if(act==='save'){saved.has(it.id)?saved.delete(it.id):saved.add(it.id); persistLists(); renderAll();} if(act==='hide'){hidden.has(it.id)?hidden.delete(it.id):hidden.add(it.id); persistLists(); renderAll();} if(act==='copy') copyShare(it,false); if(act==='share') copyShare(it,true);}
function openModal(i){modalIndex=i; modalPhotoIndex=0; const it=current[i]; if(!it)return; setModalPhoto(0); const suspect=it.residential_status&&it.residential_status!=='residential_candidate'; $('#modalPrice').textContent=fmtEuro(it.rent_eur); $('#modalTitle').textContent=it.title; $('#modalMeta').textContent=`${it.region||'Région n.c.'} · ${it.location_label||it.commune} · ${fmtM(it.surface_m2)} · ${it.rooms||'?'} pièces · ${it.bedrooms||'?'} chambre(s) · ${it.furnished} · ${it.source_site}`; $('#modalTags').innerHTML=[...statusBadges(it),...(suspect?(it.residential_reasons||[]):(it.variant_tags||[]))].map(t=>`<span class="tag">${escapeHtml(t)}</span>`).join(''); $('#modalDesc').textContent=(it.description_status?it.description_status+' — ':'')+(it.description||'Description non fournie par la source.')+'\n\n'+(suspect?('Raison exclusion : '+(it.residential_reasons||[]).join(' · ')):('Pourquoi affichée : '+(it.decision_summary||'à vérifier manuellement')))+'\n\n'+((it.trust_flags||[]).join(' · '))+((it.duplicate_group_size||1)>1?`\nSimilaires: ${it.duplicate_group_size} annonces (${(it.duplicate_sources||[]).join(', ')})`:''); $('#modalActions').innerHTML=`<button class="pillbtn" id="modalSave">${saved.has(it.id)?'★ Retirer':'☆ Sauver'}</button><button class="pillbtn" id="modalHide">${hidden.has(it.id)?'Restaurer':'Masquer'}</button><button class="pillbtn" id="modalCopy">Copier</button><button class="primary" id="modalShare">Partager</button>`; $('#modalSave').onclick=()=>handleAction('save',it); $('#modalHide').onclick=()=>handleAction('hide',it); $('#modalCopy').onclick=()=>copyShare(it,false); $('#modalShare').onclick=()=>copyShare(it,true); $('#modalLink').href=it.url; $('#modal').classList.add('open');}
function updateActionCounts(){const sm=$('#showSavedMobile'), hm=$('#showHiddenMobile'), all=$('#showAllMobile'); if(sm) sm.textContent=`★ ${saved.size}`; if(hm) hm.textContent=`Masqués ${hidden.size}`; if(all) all.textContent=`Canonique ${primaryListings().length}`;}
function renderAll(){localStorage.setItem(LS_STATE,JSON.stringify(state)); syncUrlState(); renderFilters(); renderChips(); renderZoneMap(); renderCards(); $('#q').value=state.q||''; $('#sort').value=state.sort||'score'}
function saveRender(){state.q=$('#q')?.value||''; state.sort=$('#sort')?.value||state.sort; renderAll()}
function escapeHtml(s){return String(s??'').replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]))} function escapeAttr(s){return escapeHtml(s).replace(/"/g,'&quot;')}
function clearState(){state=structuredClone(blankState); renderAll()}
function resetExample(){state=structuredClone(exampleState); renderAll()}
async function copySearchUrl(){syncUrlState(); const url=location.href; try{await navigator.clipboard.writeText(url);}catch(e){const ta=document.createElement('textarea'); ta.value=url; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();} alert('Lien de recherche copié. Il restaure les filtres, le tri et l’onglet actuel.')}
function openFilterDrawer(){document.body.classList.add('filtersOpen')} function closeFilterDrawer(){document.body.classList.remove('filtersOpen')}
$('#q').oninput=()=>{state.q=$('#q').value; saveRender()}; $('#sort').onchange=()=>{state.sort=$('#sort').value; saveRender()};
const bind=(sel,fn)=>{const el=$(sel); if(el) el.onclick=fn};
['#resetDefault','#resetDefaultMobile'].forEach(sel=>bind(sel,resetExample)); ['#copySearch','#copySearchMobile'].forEach(sel=>bind(sel,copySearchUrl)); ['#clearAll','#clearAllMobile'].forEach(sel=>bind(sel,clearState));
bind('#showAllMobile',()=>{state.tab='all'; saveRender();}); bind('#showSuspectsMobile',()=>{state.tab='suspects'; saveRender(); $('#cards')?.scrollIntoView({behavior:'smooth', block:'start'});}); bind('#showSavedMobile',()=>{state.tab='saved'; saveRender(); $('#cards')?.scrollIntoView({behavior:'smooth', block:'start'});}); bind('#showHiddenMobile',()=>{state.tab='hidden'; saveRender(); $('#cards')?.scrollIntoView({behavior:'smooth', block:'start'});});
bind('#openFilters',openFilterDrawer); bind('#closeFilters',closeFilterDrawer); bind('#scrim',closeFilterDrawer);
document.addEventListener('click',e=>{ if(e.target.closest('#openFilters')) openFilterDrawer(); if(e.target.closest('#closeFilters')||e.target.closest('#scrim')) closeFilterDrawer(); });
$('#scrollResults').onclick=()=>$('#cards').scrollIntoView({behavior:'smooth'}); $('#closeModal').onclick=()=>$('#modal').classList.remove('open'); $('#prev').onclick=()=>openModal((modalIndex-1+current.length)%current.length); $('#next').onclick=()=>openModal((modalIndex+1)%current.length); $('#photoPrev').onclick=e=>{e.stopPropagation(); setModalPhoto(-1)}; $('#photoNext').onclick=e=>{e.stopPropagation(); setModalPhoto(1)}; document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeFilterDrawer(); $('#modal').classList.remove('open')} if(!$('#modal').classList.contains('open'))return; if(e.key==='ArrowLeft')setModalPhoto(-1); if(e.key==='ArrowRight')setModalPhoto(1);});
renderFilters(); renderChips(); renderZoneMap(); renderCards(); $('#q').value=state.q||''; $('#sort').value=state.sort||'score';
</script>
</body></html>'''
safe_json=json.dumps({'meta':meta,'listings':items,'suspects':suspects}, ensure_ascii=False).replace('</', '<\\/')
html_doc=html_doc.replace('__DATA__', safe_json)
(OUT/'index.html').write_text(html_doc, encoding='utf-8')
print(json.dumps({'ok':True,'out':str(OUT),'html':str(OUT/'index.html'),'json':str(OUT/'listings.json'),'items':len(items),'input':len(rows)}, ensure_ascii=False))
