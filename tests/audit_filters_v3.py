#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sqlite3, re, sys
from pathlib import Path
from collections import Counter

BASE=Path('/opt/data/projects/reunion-immo-search')
_parser=argparse.ArgumentParser()
_parser.add_argument('--db', default=os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db'))
_parser.add_argument('--app', default=os.environ.get('IMMO_APP_PATH', str(BASE/'artifacts/app')))
_parser.add_argument('--json', default=None)
_args=_parser.parse_args()
DB=Path(_args.db)
JSON=Path(_args.json) if _args.json else Path(_args.app)/'listings.json'

TARGET=['Sainte-Marie','Moufia','La Bretagne','Saint-Denis']

def norm(s):
    return re.sub(r'\s+',' ',str(s or '')).strip().lower().replace('-', ' ')

def load():
    data=json.loads(JSON.read_text())
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    rows=[dict(r) for r in con.execute("SELECT * FROM rental_listings WHERE COALESCE(is_active,1)=1")]
    return rows, data.get('listings') or [], data.get('meta') or {}

HARD_NON_RESIDENTIAL_RE=re.compile(r'\b(local\s+(?:commercial|professionnel|m[eé]dical)|locaux\s+commerciaux|bureau(?:x)?\b|box\b|parking\b|garage\b|garde\s*-?\s*meuble|terrain\b|entrep[oô]t)\b', re.I)

def exported_row_ok(r):
    title=str(r.get('title') or '')
    desc=str(r.get('description') or '')
    ptype=norm(r.get('property_type'))
    url=str(r.get('url') or '')
    reasons=[]
    if ptype in {'commercial','box'}:
        reasons.append('property_type')
    if HARD_NON_RESIDENTIAL_RE.search(' '.join([title, ptype, url])):
        reasons.append('strong_keyword')
    if re.search(r'\b(local\s+(?:commercial|professionnel|m[eé]dical)|locaux\s+commerciaux)\b', desc, re.I):
        reasons.append('description_keyword')
    if r.get('source_site')=='seloger' and '/annonces/' in url and '-974/' not in url:
        reasons.append('outside_reunion_url')
    return bool(r.get('image_url')) and not reasons and not (r.get('rent_eur') and r.get('rent_eur')>10000)

def matches(item, st):
    text=norm(' '.join(str(item.get(k) or '') for k in ['title','city','region','location','district','description','description_status','source','type']))
    if st.get('q') and norm(st['q']) not in text: return False
    if st.get('region') and item.get('region') not in st['region']: return False
    if st.get('city') and item.get('city') not in st['city']: return False
    if st.get('property_type') and item.get('type') not in st['property_type']: return False
    if st.get('furnished') and item.get('furnished') not in st['furnished']: return False
    if st.get('rentMin') and (not item.get('price') or item['price']<st['rentMin']): return False
    if st.get('rentMax') and (not item.get('price') or item['price']>st['rentMax']): return False
    if st.get('surfaceMin') and (not item.get('surface') or item['surface']<st['surfaceMin']): return False
    if st.get('roomsMin') and (not item.get('rooms') or item['rooms']<st['roomsMin']): return False
    if st.get('bedroomsMin') and (not item.get('bedrooms') or item['bedrooms']<st['bedroomsMin']): return False
    return True

def strict(item):
    return item.get('region') == 'Nord' and norm(item.get('location')).find('sainte marie') >= 0 and (item.get('surface') or 0)>=50 and 600 <= (item.get('price') or -1) <= 1000 and ((item.get('bedrooms') or 0)>=1 or (item.get('rooms') or 0)>=2)

def main():
    db_rows, items, meta=load()
    ids={x['id'] for x in items}
    expected_export=[r for r in db_rows if exported_row_ok(r)]
    issues=[]
    if len(items)!=len(ids): issues.append(f'duplicate ids: {len(items)-len(ids)}')
    if len(items) < 500: issues.append(f'json_items too low: {len(items)}')
    required=['region','location','description','description_status','opportunity_analysis','image_url','url','title']
    for k in required:
        missing=sum(1 for x in items if not x.get(k))
        if missing: issues.append(f'missing {k}: {missing}')
    bad_img=sum(1 for x in items if not str(x.get('image_url','')).startswith(('http://','https://')))
    if bad_img: issues.append(f'bad image urls: {bad_img}')
    scenarios={
        'default_nord_budget': {'region':['Nord'], 'property_type':['Appartement','Maison','Appartement / maison'], 'rentMin':600, 'rentMax':1000, 'surfaceMin':40, 'roomsMin':2},
        'moufia_any': {'q':'Moufia'},
        'saint_pierre_sud': {'region':['Sud'], 'q':'Saint-Pierre'},
        'sud_budget_surface': {'region':['Sud'], 'rentMin':600, 'rentMax':1000, 'surfaceMin':50},
        'all_clear': {},
    }
    scenario_counts={name: sum(1 for x in items if matches(x, st)) for name, st in scenarios.items()}
    strict_count=sum(1 for x in items if strict(x))
    print('AUDIT_FILTERS_V3')
    print('db_active_rows', len(db_rows))
    print('expected_export_from_db_rules', len(expected_export))
    print('json_items', len(items))
    print('regions', dict(Counter(x.get('region') for x in items)))
    print('description_status', dict(Counter(x.get('description_status') for x in items)))
    print('strict_count', strict_count)
    print('scenario_counts', scenario_counts)
    if issues:
        print('ISSUES')
        for i in issues: print('-', i)
        sys.exit(1)
    print('AUDIT_OK')

if __name__=='__main__': main()
