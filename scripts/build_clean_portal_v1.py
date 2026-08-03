#!/usr/bin/env python3
from __future__ import annotations

import json, hashlib, shutil, statistics, re
from pathlib import Path
from datetime import datetime, timezone

import os
from media_link_copy import copytree_media_aware

ROOT = Path('/opt/data/projects/reunion-immo-search')
SRC_APP = Path(os.environ.get('IMMO_APP_PATH', str(ROOT / 'artifacts/app')))
SRC_JSON = SRC_APP / 'listings.json'
OUT = Path(os.environ.get('IMMO_OUT_PATH', str(ROOT / 'artifacts/app_clean_v1')))
MEDIA_COPY_MODE = os.environ.get('IMMO_MEDIA_COPY_MODE', 'copy')
THUMBS = SRC_APP / 'thumbs'

raw = json.loads(SRC_JSON.read_text(encoding='utf-8'))
items = raw.get('listings', raw if isinstance(raw, list) else [])
thumb_names = {p.name for p in THUMBS.glob('*') if p.is_file()}

# clean output
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
if THUMBS.exists():
    copytree_media_aware(THUMBS, OUT / 'thumbs', media_mode=MEDIA_COPY_MODE)
for side_name in ('source_health.json', 'changes.json', 'photo_quality.json'):
    src_side = SRC_APP / side_name
    if src_side.exists():
        shutil.copy2(src_side, OUT / side_name)

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


def bathroom_state(it: dict) -> dict:
    raw = it.get('bathroom')
    src = raw if isinstance(raw, dict) else {}
    if src.get('state') and src.get('label'):
        return {'state': src.get('state'), 'label': src.get('label')}
    bathtub = it.get('bathtub')
    nb_sdb = it.get('nb_sdb')
    if bathtub == 1:
        return {'state': 'baignoire', 'label': 'baignoire'}
    if bathtub == 0:
        return {'state': 'douche_seulement', 'label': 'douche seulement'}
    if nb_sdb:
        return {'state': 'salle_de_bain_equipement_inconnu', 'label': 'salle de bain, équipement non précisé'}
    return {'state': 'non_precise', 'label': 'non précisé'}


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
    analysis = analyze_description({**it, 'price': int(price) if isinstance(price,(int,float)) else price})
    feature_tags = []
    bath = bathroom_state(it)
    furnished_status = ((analysis.get('property_state') or {}).get('furnished') or {}).get('status')
    if furnished_status == 'meuble':
        feature_tags.append('Meublé')
    elif furnished_status == 'non_meuble':
        feature_tags.append('Non meublé')
    feats = analysis.get('features') or {}
    if ((feats.get('parking') or {}).get('present')):
        feature_tags.append('Parking')
    outdoor = ((feats.get('outdoor') or {}).get('evidence') or [])
    outdoor_rules = ' '.join(str(x.get('rule') or x.get('text') or '') for x in outdoor).lower()
    if any(k in outdoor_rules for k in ['terrace', 'balcony', 'varangue']):
        feature_tags.append('Varangue / terrasse')
    if 'garden' in outdoor_rules:
        feature_tags.append('Jardin')
    if 'pool' in outdoor_rules:
        feature_tags.append('Piscine')
    if bath.get('state') and bath.get('state') != 'non_precise':
        feature_tags.append(bath['label'])
    feature_tags = uniq(feature_tags)
    image_quality = {
        'version': 'image_quality_v1',
        'valid_local_primary': bool(local_primary),
        'valid_local_count': len(local_gallery),
        'external_count': len(external_gallery),
        'status': 'local' if local_primary else 'external_only' if external_gallery else 'missing',
    }
    geo_quality = it.get('geo_quality') or {
        'level': (loc_intel.get('quality') or 'commune'),
        'confidence': loc_intel.get('confidence'),
        'method': loc_intel.get('method') or 'exported_location_intelligence',
    }
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
        'bathroom': bath,
        'agency': it.get('agency_or_owner') or '',
        'image_url': it.get('image_url'),
        'image_urls': external_gallery,
        'local_image_url': local_primary,
        'local_image_urls': local_gallery,
        'description': it.get('description') or '',
        'description_status': it.get('description_status') or '',
        'description_analysis': analysis,
        'feature_tags': feature_tags,
        'image_quality': image_quality,
        'geo_quality': geo_quality,
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
INDEX_FIELDS = ('id', 'source', 'source_id', 'title', 'city', 'location', 'region', 'type', 'price', 'surface', 'rooms', 'bedrooms', 'bathroom', 'score', 'local_image_url', 'seen_last_at')
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
    'valid_local_images': local_count,
    'photo_quality_issues': sum(1 for x in clean if not (x.get('image_quality') or {}).get('valid_local_primary')),
    'geo_confidence': {k: sum(1 for x in clean if (x.get('geo_quality') or {}).get('level') == k) for k in ['haute','moyenne','commune','basse']},
    'amenity_tags': sum(1 for x in clean if x.get('feature_tags')),
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
photo_summary = {
    'version': 'photo_quality_v1',
    'summary': {
        'total': len(clean),
        'with_valid_local': local_count,
        'missing_local': len(clean) - local_count,
        'with_gallery': sum(1 for x in clean if len(x.get('local_image_urls') or []) > 1),
    },
    'items': [{'id': x.get('id'), **(x.get('image_quality') or {})} for x in clean],
}
(OUT / 'photo_quality.json').write_text(json.dumps(photo_summary, ensure_ascii=False, indent=2), encoding='utf-8')
search_ontology = {
    'version': 'natural_search_v2',
    'commune_aliases': {
        'Saint-Denis': ['saint denis','st denis','sainte clotilde'],
        'Sainte-Marie': ['sainte marie','ste marie','beausejour','grande montée','les cafés'],
        'Saint-Paul': ['saint paul','st paul','bois de nèfles saint-paul'],
        'Saint-Pierre': ['saint pierre','st pierre'],
    },
    'amenity_aliases': {
        'Meublé': ['meublé','meublee','meuble'],
        'Parking': ['parking','garage','stationnement'],
        'Varangue / terrasse': ['varangue','terrasse','balcon'],
    },
}
(OUT / 'search_ontology.json').write_text(json.dumps(search_ontology, ensure_ascii=False, indent=2), encoding='utf-8')
PUBLIC_BASE = 'https://immo.148.230.103.174.sslip.io'
PUBLIC_PAGES = ['v2/']
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


print(json.dumps({'out': str(OUT), 'listings': len(clean), 'local_photos': local_count, 'coverage': coverage}, ensure_ascii=False, indent=2))
