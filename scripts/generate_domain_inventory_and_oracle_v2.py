#!/usr/bin/env python3
"""Generate domain inventory and a broad business oracle for Immo RUN.

This converts the data into acceptance families. Moufadal should not have
to enumerate variants; this script derives them from the product domain.
"""
from __future__ import annotations
import json, re, statistics
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.reunion_geo_search_contract import BUSINESS_LOCATIONS, norm, variants

DEFAULT_APP = ROOT / 'artifacts' / 'app'
APP = DEFAULT_APP
LISTINGS = APP / 'listings.json'
CHANGES = APP / 'changes.json'
OUT_INV = ROOT / 'artifacts' / 'domain_inventory_v2.json'
OUT_ORACLE = ROOT / 'tests' / 'acceptance_search_oracle_v2.json'



def price(x: dict[str, Any]) -> int | None:
    for k in ('price', 'rent_eur'):
        v = x.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
        if isinstance(v, str):
            m = re.search(r'\d[\d\s.,]*', v)
            if m:
                try:
                    return int(float(m.group(0).replace(' ', '').replace(',', '.')))
                except Exception:
                    pass
    return None


def room_count(x: dict[str, Any]) -> int | None:
    for k in ('rooms', 'pieces'):
        v = x.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
        if isinstance(v, str):
            m = re.search(r'\d+', v)
            if m:
                return int(m.group(0))
    title = norm(x.get('title'))
    m = re.search(r'\b[tf]\s*([1-9])\b', title)
    if m:
        return int(m.group(1))
    return None


def text_blob(x: dict[str, Any]) -> str:
    parts = [x.get(k) for k in ('title','description','city','district','location','region','type')]
    parts += x.get('feature_tags') or []
    return norm(' '.join(str(p or '') for p in parts))


def uniq(seq: list[str]) -> list[str]:
    out: list[str] = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    global APP, LISTINGS, CHANGES
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default=str(DEFAULT_APP))
    ap.add_argument('--inventory-out', default=str(OUT_INV), help='Inventory JSON output path. Defaults to artifacts/domain_inventory_v2.json.')
    ap.add_argument('--oracle-out', default=str(OUT_ORACLE), help='Oracle JSON output path. Defaults to tests/acceptance_search_oracle_v2.json.')
    args = ap.parse_args()
    APP = Path(args.app)
    LISTINGS = APP / 'listings.json'
    CHANGES = APP / 'changes.json'
    inventory_out = Path(args.inventory_out)
    oracle_out = Path(args.oracle_out)
    payload = json.loads(LISTINGS.read_text())
    items = payload.get('listings') or []
    changes = json.loads(CHANGES.read_text()) if CHANGES.exists() else {'changes': []}

    cities, districts, regions, types, sources = Counter(), Counter(), Counter(), Counter(), Counter()
    features = Counter()
    prices, surfaces, rooms = [], [], []
    furnished_yes = furnished_no = furnished_unknown = 0
    nulls = Counter()

    for x in items:
        cities[x.get('city') or 'Non précisée'] += 1
        if x.get('district'):
            districts[x.get('district')] += 1
        regions[x.get('region') or 'Non précisée'] += 1
        types[x.get('type') or 'Non précisé'] += 1
        sources[x.get('source') or 'unknown'] += 1
        for f in x.get('feature_tags') or []:
            features[str(f)] += 1
        p = price(x)
        if p:
            prices.append(p)
        s = x.get('surface') or x.get('surface_m2')
        try:
            if s:
                surfaces.append(float(str(s).replace(',', '.')))
        except Exception:
            pass
        r = room_count(x)
        if r:
            rooms.append(r)
        f = x.get('furnished')
        blob = text_blob(x)
        if f is True or ' meuble ' in f' {blob} ' or ' meublee ' in f' {blob} ':
            furnished_yes += 1
        elif f is False:
            furnished_no += 1
        else:
            furnished_unknown += 1
        for key in ('price','surface','rooms','city','type','url','local_image_url'):
            if not x.get(key):
                nulls[key] += 1

    q = lambda arr, pct: int(statistics.quantiles(arr, n=100)[pct-1]) if len(arr) >= 100 else None
    inv = {
        'version': 'domain_inventory_v2',
        'listing_count': len(items),
        'cities': cities.most_common(),
        'districts': districts.most_common(),
        'regions': regions.most_common(),
        'types': types.most_common(),
        'sources': sources.most_common(),
        'features': features.most_common(80),
        'price_summary': {
            'count': len(prices), 'min': min(prices) if prices else None, 'max': max(prices) if prices else None,
            'p25': q(prices, 25), 'median': int(statistics.median(prices)) if prices else None, 'p75': q(prices, 75)
        },
        'surface_summary': {
            'count': len(surfaces), 'min': min(surfaces) if surfaces else None, 'max': max(surfaces) if surfaces else None,
            'median': statistics.median(surfaces) if surfaces else None
        },
        'rooms': Counter(rooms).most_common(),
        'furnished': {'yes': furnished_yes, 'no_explicit': furnished_no, 'unknown_or_not_furnished': furnished_unknown},
        'missing_fields': nulls.most_common(),
        'changes': Counter(c.get('event_type') for c in changes.get('changes', [])).most_common(),
    }

    cases: list[dict[str, Any]] = []
    def add(cid: str, query: str, family: str, **expect: Any) -> None:
        cases.append({'id': cid, 'query': query, 'family': family, 'expect': expect})

    # Locations: top cities/districts + a product ontology of Réunion quartiers/lieux-dits.
    # This protects against the old failure mode: fixing only the exact user examples.
    business_locations = BUSINESS_LOCATIONS
    loc_labels = []
    for label, count in cities.most_common(12) + districts.most_common(12):
        if label and label != 'Non précisée':
            loc_labels.append(label)
    loc_labels += list(business_locations)
    seen = set()
    for label in loc_labels:
        if norm(label) in seen:
            continue
        seen.add(norm(label))
        business = business_locations.get(label)
        raw_variants = list(business.get('aliases') if business else variants(label))
        # city/commune searches are allowed to match the city; exact quartier searches
        # must be justified by the quartier alias itself, not only by the broader commune.
        result_hay_any = raw_variants if business and business.get('exact') else variants(label)
        for i, v in enumerate(uniq(raw_variants)[:6]):
            add(
                f'loc_{norm(label).replace(" ","_")}_{i}',
                v,
                'location',
                understood_any=[label.split('(')[0].strip().split(',')[0].strip()],
                no_all_base=True,
                result_hay_any=result_hay_any,
            )

    # Rooms variants.
    for n, _ in Counter(dict(inv['rooms'])).most_common(5):
        if 1 <= int(n) <= 6:
            n = int(n)
            for qv in [f'T{n}', f'F{n}', f'T {n}', f'F {n}', f'{n} pièces', f'{n} pieces']:
                add(f'rooms_{n}_{norm(qv).replace(" ","_")}', qv, 'rooms', understood_any=[f'T/F{n}', f'{n}'], no_zero=True)
    add('rooms_studio', 'studio', 'rooms', understood_any=['Studio', 'T1'], no_zero=True)

    # Furnished / non furnished.
    for qv in ['meublé', 'meuble', 'location meublée']:
        add(f'furnished_yes_{norm(qv).replace(" ","_")}', qv, 'furnished', understood_any=['Meublé'], no_zero=True)
    for qv in ['non meublé', 'non meuble', 'pas meublé', 'sans meublé', 'location nue', 'loué vide']:
        add(f'furnished_no_{norm(qv).replace(" ","_")}', qv, 'furnished_negation', understood_any=['Non meublé'], no_zero=True, not_understood_any=[' Meublé'])

    # Budgets based around common thresholds.
    thresholds = [700, 900, 1000, 1200, 1500]
    if inv['price_summary']['median']:
        thresholds.append(int(inv['price_summary']['median']))
    for t in sorted(set(thresholds)):
        add(f'budget_bare_{t}', str(t), 'budget_ambiguous', understood_any=['à préciser'], has_price_buttons=True)
        add(f'budget_max_{t}', f'moins {t}', 'budget_max', understood_any=[f'≤ {t}'], no_zero=True)
        add(f'budget_under_{t}', f'sous {t}', 'budget_max', understood_any=[f'≤ {t}'], no_zero=True)
        add(f'budget_min_{t}', f'plus {t}', 'budget_min', understood_any=[f'≥ {t}'], no_zero=True)
        add(f'budget_equal_{t}', f'exact {t}', 'budget_equal', understood_any=[f'= {t}'])
        add(f'budget_around_{t}', f'autour de {t}', 'budget_around', understood_any=['autour'])
    add('budget_between_800_1000', 'entre 800 et 1000', 'budget_range', understood_any=['800', '1 000'], no_zero=True)

    # Types and features.
    for label, _ in types.most_common(8):
        if label and 'non' not in norm(label):
            add(f'type_{norm(label).replace(" ","_")}', label, 'type', understood_any=[label.split()[0]], no_all_base=True)
    for label, _ in features.most_common(12):
        add(f'feature_{norm(label).replace(" ","_")}', label, 'feature', understood_any=[label], no_all_base=True)

    # Realistic combinations.
    combos = [
        'F4 non meublé plus 1000', 'T3 moins 900', 'studio meublé moins 800',
        'appartement T2 autour de 900', 'maison 4 pièces jardin',
        'Saint-Denis T2 moins 1000', 'Sainte-Marie F4 non meublé',
        'Beauséjour T2 moins 900', 'Grande Montée T5', 'Rivière des Pluies maison',
    ]
    for i, qv in enumerate(combos):
        add(f'combo_{i}', qv, 'combination', no_crash=True)

    oracle = {
        'version': 'acceptance_search_oracle_v2',
        'principle': 'Generated from product domain inventory; tests families, not only user examples.',
        'source_inventory': display_path(inventory_out),
        'case_count': len(cases),
        'cases': cases,
        'interaction_cases': [
            {'id':'budget_button_max','start':'900','click':'max','expect_query':'moins 900','expect_understood_any':['≤ 900']},
            {'id':'budget_button_min','start':'900','click':'min','expect_query':'plus 900','expect_understood_any':['≥ 900']},
            {'id':'budget_button_equal','start':'900','click':'equal','expect_query':'= 900','expect_understood_any':['= 900']},
            {'id':'budget_button_around','start':'900','click':'around','expect_query':'autour de 900','expect_understood_any':['autour']},
            # Regression family from Moufadal's complaint: budget operator clicks inside a
            # full natural query must preserve the rest of the intent and synchronize the
            # input + criteria line. Bare-number-only tests are not sufficient.
            {
                'id':'budget_button_around_preserves_combo_beausejour',
                'start':'F4 non meublé Beauséjour 900',
                'click':'around',
                'expect_query':'F4 non meublé Beauséjour autour de 900',
                'expect_understood_any':['T/F4','Beauséjour','Non meublé','Autour de 900'],
            },
            {
                'id':'budget_button_max_preserves_combo_grande_montee',
                'start':'T4 non meublé Grande Montée 1300',
                'click':'max',
                'expect_query':'T4 non meublé Grande Montée moins 1300',
                'expect_understood_any':['T/F4','Grande Montée','Non meublé','≤ 1300'],
            },
        ]
    }

    inventory_out.parent.mkdir(parents=True, exist_ok=True)
    oracle_out.parent.mkdir(parents=True, exist_ok=True)
    inventory_out.write_text(json.dumps(inv, ensure_ascii=False, indent=2))
    oracle_out.write_text(json.dumps(oracle, ensure_ascii=False, indent=2))
    print(json.dumps({'ok': True, 'inventory': str(inventory_out), 'oracle': str(oracle_out), 'cases': len(cases)}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
