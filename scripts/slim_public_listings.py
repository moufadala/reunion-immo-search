#!/usr/bin/env python3
"""Slim public listings.json without changing product semantics.

The public UI needs searchable/displayable facts, not every raw analysis blob.
This keeps fields used by index.html/tests and removes heavy debug/internal payloads.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_APP = Path('/opt/data/projects/reunion-immo-search/artifacts/app')

KEEP_TOP = {
    'id','source','source_id','url','title','city','district','region','location',
    'location_intelligence','map_point','map_url','type','price','surface','rooms','bedrooms',
    'furnished','bathroom','agency','image_url','local_image_url','local_image_urls','description',
    'description_status','description_analysis','seen_last_at','published_at','score',
    'opportunity_score','opportunity_analysis','feature_tags','geo_quality','image_quality','seen_also_on','dedup_product_note',
    'dedup_group_id','dedup_decision','dedup_confidence','dedup_role','dedup_reason','dedup_sources','canonical_display_id','display_canonical','housing_details'
}

def preserve_local_gallery(urls):
    """Keep every distinct local photo; completeness matters more than JSON size."""
    if not isinstance(urls, list):
        return []
    return list(dict.fromkeys(str(url) for url in urls if url))


def slim_location(li):
    if not isinstance(li, dict):
        return li
    keep = ['district_best','precise_location_label','commune_inferred','district_hints']
    out = {k: li.get(k) for k in keep if li.get(k) not in (None,'',[],{})}
    # Keep only first few hints; enough for search truth, avoids bloat.
    if isinstance(out.get('district_hints'), list):
        out['district_hints'] = out['district_hints'][:5]
    return out

def slim_description_analysis(da):
    if not isinstance(da, dict):
        return da
    status = (((da.get('property_state') or {}).get('furnished') or {}).get('status'))
    if status:
        return {'property_state': {'furnished': {'status': status}}}
    return {}

def slim_housing(h):
    if not isinstance(h, dict):
        return h
    out = {}
    for k in ['bathrooms','wc','layout','bedroom_distribution']:
        v = h.get(k)
        if not isinstance(v, dict):
            continue
        vv = {kk: v.get(kk) for kk in ['count','label','ground_floor_count','upstairs_count','status'] if v.get(kk) not in (None,'',[],{})}
        ev = v.get('evidence')
        if isinstance(ev, list) and ev:
            vv['evidence'] = ev[:2]
        if vv:
            out[k] = vv
    return out

def slim_opportunity(oa):
    """Keep the public opportunity contract while dropping bulky debug detail."""
    if not isinstance(oa, dict):
        return oa
    keep = ['score', 'label', 'segment', 'reasons', 'risks', 'signals', 'recommendation']
    out = {k: oa.get(k) for k in keep if oa.get(k) not in (None, '', [], {})}
    for k in ['reasons', 'risks', 'signals']:
        if isinstance(out.get(k), list):
            out[k] = out[k][:6]
    return out

TECH_DESC_RE = re.compile(r'Annonce\s+SeLoger\s+collectée\s+par\s+CDP\..*$', re.I | re.S)

def clean_description(y):
    """Remove crawler/debug prose from public descriptions."""
    desc = str(y.get('description') or '').strip()
    if desc and not TECH_DESC_RE.search(desc) and not any(t in desc for t in ('serp_view', 'distributionTypes', 'collectée par CDP')):
        return desc
    bits = []
    if y.get('type'):
        bits.append(str(y['type']))
    if y.get('rooms'):
        bits.append(f"{y['rooms']} pièce(s)")
    if y.get('surface'):
        bits.append(f"{y['surface']} m²")
    loc = y.get('district') or y.get('city') or y.get('location')
    if loc:
        bits.append(str(loc))
    if y.get('price'):
        bits.append(f"{y['price']} €/mois")
    return ' · '.join(bits) if bits else ''

def public_price_ok(x):
    try:
        p = float(x.get('price'))
    except Exception:
        return False
    return p > 0

INDEX_FIELDS = (
    'id', 'source', 'source_id', 'title', 'city', 'district', 'location', 'region',
    'type', 'price', 'surface', 'rooms', 'bedrooms', 'furnished', 'score',
    'opportunity_score','local_image_url','seen_last_at','feature_tags','bathroom',
)

def write_light_index(app: Path, generated_at, items: list[dict]) -> None:
    index_items = [
        {k: x.get(k) for k in INDEX_FIELDS if x.get(k) not in (None, '', [], {})}
        for x in items
    ]
    out = {
        'generated_at': generated_at,
        'count': len(index_items),
        'listings': index_items,
        'note': 'Index léger dérivé de listings.json après filtrage public; les fiches complètes restent dans listings.json.',
    }
    (app / 'listings_index.json').write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

def refresh_coverage(app: Path, items: list[dict]) -> None:
    cov_path = app / 'coverage.json'
    if not cov_path.exists():
        return
    cov = json.loads(cov_path.read_text(encoding='utf-8'))
    prices = [v for x in items for v in [x.get('price')] if isinstance(v, (int, float))]
    surfaces = [v for x in items for v in [x.get('surface')] if isinstance(v, (int, float))]
    cities = [str(x.get('city')) for x in items if x.get('city')]
    regions = [str(x.get('region')) for x in items if x.get('region')]
    types = [str(x.get('type')) for x in items if x.get('type')]
    cov.update({
        'count': len(items),
        'local_primary_photos': sum(1 for x in items if x.get('local_image_url')),
        'external_primary_photos': sum(1 for x in items if x.get('image_url')),
        'gallery_photos': sum(1 for x in items if len(x.get('local_image_urls') or []) > 1),
        'image_urls_multi': sum(1 for x in items if len(x.get('image_urls') or []) > 1),
        'cities': len(set(cities)),
        'regions': sorted(set(regions)),
        'types': sorted(set(types)),
        'price_min': min(prices) if prices else None,
        'price_max': max(prices) if prices else None,
        'surface_min': min(surfaces) if surfaces else None,
        'surface_max': max(surfaces) if surfaces else None,
        'valid_local_images': sum(1 for x in items if (x.get('image_quality') or {}).get('valid_local_count')),
        'photo_quality_issues': sum(1 for x in items if (x.get('image_quality') or {}).get('issues')),
        'geo_confidence': {level: sum(1 for x in items if (x.get('geo_quality') or {}).get('level') == level) for level in ['haute','moyenne','commune','faible']},
    })
    amenity_tags = cov.get('amenity_tags')
    if isinstance(amenity_tags, dict):
        cov['amenity_tags'] = {tag: sum(1 for x in items if tag in (x.get('feature_tags') or [])) for tag in amenity_tags}
    cov_path.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding='utf-8')

def refresh_photo_quality(app: Path, items: list[dict]) -> None:
    photo_path = app / 'photo_quality.json'
    if not photo_path.exists():
        return
    photo = json.loads(photo_path.read_text(encoding='utf-8'))
    photo['summary'] = {
        'listings': len(items),
        'with_local_primary': sum(1 for x in items if x.get('local_image_url')),
        'with_local_gallery': sum(1 for x in items if len(x.get('local_image_urls') or []) > 1),
        'with_valid_local': sum(1 for x in items if (x.get('image_quality') or {}).get('valid_local_count')),
        'with_issues': sum(1 for x in items if (x.get('image_quality') or {}).get('issues')),
    }
    photo['issues'] = [
        {'id': x.get('id'), 'source': x.get('source'), 'title': x.get('title'), 'image_quality': x.get('image_quality')}
        for x in items
        if (x.get('image_quality') or {}).get('issues') or not (x.get('image_quality') or {}).get('valid_local_count')
    ][:300]
    photo_path.write_text(json.dumps(photo, ensure_ascii=False, indent=2), encoding='utf-8')

def refresh_locations(app: Path, items: list[dict]) -> None:
    loc_path = app / 'locations.json'
    if not loc_path.exists():
        return
    public_ids = {str(x.get('id')) for x in items if x.get('id')}
    loc = json.loads(loc_path.read_text(encoding='utf-8'))
    loc_items = [x for x in (loc.get('listings') or []) if str(x.get('id')) in public_ids]
    loc['listings'] = loc_items
    quality: dict[str, int] = {}
    commune_counts: dict[str, int] = {}
    for x in loc_items:
        q = str(x.get('quality') or x.get('confidence') or 'inconnue')
        quality[q] = quality.get(q, 0) + 1
        c = str(x.get('commune_inferred') or 'Non précisée')
        commune_counts[c] = commune_counts.get(c, 0) + 1
    summary = loc.get('summary') if isinstance(loc.get('summary'), dict) else {}
    summary.update({
        'listings': len(loc_items),
        'quality': dict(sorted(quality.items())),
        'top_communes': sorted(commune_counts.items(), key=lambda kv: kv[1], reverse=True),
        'north_east_listings': sum(1 for x in loc_items if x.get('north_east_focus')),
    })
    loc['summary'] = summary
    loc_path.write_text(json.dumps(loc, ensure_ascii=False, indent=2), encoding='utf-8')

def refresh_opportunity(app: Path, items: list[dict]) -> None:
    opp_path = app / 'opportunity.json'
    if not opp_path.exists():
        return
    public_ids = {str(x.get('id')) for x in items if x.get('id')}
    opp = json.loads(opp_path.read_text(encoding='utf-8'))
    if isinstance(opp.get('top'), list):
        opp['top'] = [x for x in opp['top'] if str(x.get('id')) in public_ids]
    opp_path.write_text(json.dumps(opp, ensure_ascii=False, indent=2), encoding='utf-8')

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description='Slim public listings.json while preserving fields required by public gates.')
    ap.add_argument('app', nargs='?', type=Path, default=DEFAULT_APP, help='Public app/stage directory containing listings.json')
    return ap.parse_args(argv)

def main(argv: list[str] | None = None):
    args = parse_args(argv)
    app = args.app
    path = app / 'listings.json'
    payload = json.loads(path.read_text(encoding='utf-8'))
    before = path.stat().st_size
    new_items = []
    excluded = {'missing_or_invalid_price': 0}
    for x in payload.get('listings', []):
        if not public_price_ok(x):
            excluded['missing_or_invalid_price'] += 1
            continue
        y = {k: x.get(k) for k in KEEP_TOP if k in x and x.get(k) not in (None,'',[],{})}
        if 'location_intelligence' in y:
            y['location_intelligence'] = slim_location(y['location_intelligence'])
        if 'description_analysis' in y:
            y['description_analysis'] = slim_description_analysis(y['description_analysis'])
        if 'housing_details' in y:
            y['housing_details'] = slim_housing(y['housing_details'])
        if 'opportunity_analysis' in y:
            y['opportunity_analysis'] = slim_opportunity(y['opportunity_analysis'])
        if 'description' in y:
            y['description'] = clean_description(y)
        if 'feature_tags' not in y:
            y['feature_tags'] = []
        if isinstance(y.get('local_image_urls'), list):
            y['local_image_urls'] = preserve_local_gallery(y['local_image_urls'])
        new_items.append(y)
    generated_at = payload.get('generated_at')
    out = {'generated_at': generated_at, 'count': len(new_items), 'listings': new_items}
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    write_light_index(app, generated_at, new_items)
    refresh_coverage(app, new_items)
    refresh_photo_quality(app, new_items)
    refresh_locations(app, new_items)
    refresh_opportunity(app, new_items)
    # Backups in app root accidentally become public budget debt. Move them out of served app.
    backup_dir = app.parent / 'app_backups'
    backup_dir.mkdir(exist_ok=True)
    moved=[]
    for p in app.glob('listings.before-*.json'):
        target = backup_dir / p.name
        if target.exists():
            p.unlink()
        else:
            p.rename(target)
        moved.append(str(target))
    after = path.stat().st_size
    print(json.dumps({'ok': True, 'before': before, 'after': after, 'moved_backups': moved, 'excluded': excluded, 'public_count': len(new_items)}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
