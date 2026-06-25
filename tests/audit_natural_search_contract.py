#!/usr/bin/env python3
from __future__ import annotations
import json, re, sys
from pathlib import Path

APP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/data/projects/reunion-immo-search/artifacts/app')
errors=[]
index=APP/'index.html'
listings=APP/'listings.json'
ontology=APP/'search_ontology.json'
for p in [index,listings,ontology]:
    if not p.exists() or p.stat().st_size == 0:
        errors.append(f'missing_or_empty {p.name}')
html=index.read_text(encoding='utf-8', errors='replace') if index.exists() else ''
required_tokens=['naturalSearchV2','nlChips','understood','suggestions','parseNatural','SEARCH_ONTOLOGY','data-chip-remove','matchReasons','geoHint']
for t in required_tokens:
    if t not in html:
        errors.append(f'missing token in index: {t}')
if 'Ex: T2 Saint-Denis moins 900€ meublé parking' not in html:
    errors.append('placeholder does not teach natural search')
if html.count('id="naturalSearchV2"') != 1:
    errors.append(f'naturalSearchV2 script count != 1: {html.count("id=\"naturalSearchV2\"")}')
if ontology.exists():
    o=json.loads(ontology.read_text(encoding='utf-8'))
    if o.get('version') != 'natural_search_v2': errors.append('ontology version mismatch')
    for city in ['Saint-Denis','Sainte-Marie','Saint-Paul','Saint-Pierre']:
        if city not in o.get('commune_aliases',{}): errors.append(f'missing city alias {city}')
    for amenity in ['Meublé','Parking','Varangue / terrasse']:
        if amenity not in o.get('amenity_aliases',{}): errors.append(f'missing amenity alias {amenity}')
if listings.exists():
    data=json.loads(listings.read_text(encoding='utf-8'))
    items=data.get('listings') or []
    if len(items) < 400: errors.append(f'too few listings {len(items)}')
    for field in ['feature_tags','geo_quality','image_quality']:
        c=sum(1 for x in items if field in x)
        if c != len(items): errors.append(f'{field} not present on all listings: {c}/{len(items)}')
    if sum(1 for x in items if x.get('feature_tags')) < 50:
        errors.append('too few feature-tagged listings; natural amenities will feel weak')
    if sum(1 for x in items if (x.get('geo_quality') or {}).get('level') in {'haute','moyenne','commune'}) < 400:
        errors.append('too few geo-quality labels')
print(json.dumps({'ok':not errors,'app':str(APP),'errors':errors}, ensure_ascii=False, indent=2))
if errors:
    raise SystemExit(1)
print('NATURAL_SEARCH_CONTRACT_AUDIT PASS')
