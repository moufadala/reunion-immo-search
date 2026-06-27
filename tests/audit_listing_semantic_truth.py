#!/usr/bin/env python3
"""Semantic truth audit for Immo RUN listings.

Classifies product data as OK / uncertain / hide / error. This is not just display equality:
it checks whether fields are useful and safe to show to a normal property-search user.
"""
from __future__ import annotations
import json, re, unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'artifacts' / 'app'
LISTINGS = APP / 'listings.json'
OUT = ROOT / 'artifacts' / 'listing_semantic_truth_v2.json'

RE_TECH = re.compile(r'(serp_view|distributionTypes|CDP|undefined|\bnull\b|null m²|\bNone\b|\bNaN\b|object Object)', re.I)
HOUSING_TYPES = {'appartement','maison','studio','duplex','villa','chambre','t1','t2','t3','t4','t5','t6'}
COMMERCIAL_HINTS = re.compile(r'\b(local commercial|bureau|commerce|entrep[oô]t|professionnel|local professionnel)\b', re.I)


def norm(s: Any) -> str:
    s = str(s or '').lower()
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def as_num(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r'\d[\d\s.,]*', v)
        if m:
            try:
                return float(m.group(0).replace(' ', '').replace(',', '.'))
            except Exception:
                return None
    return None


def classify(x: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    errors: list[str] = []
    uncertain: list[str] = []
    hide: list[str] = []
    blob_raw = ' '.join(str(x.get(k) or '') for k in ('title','description','type','city','district','location'))
    blob = norm(blob_raw)

    if not x.get('id') or not x.get('url'):
        errors.append('missing_id_or_url')
    p = as_num(x.get('price'))
    if p is None or p <= 0:
        errors.append('missing_or_invalid_price')
    elif p < 250 or p > 10000:
        uncertain.append(f'price_outlier={p:g}')
    s = as_num(x.get('surface'))
    if s is None:
        uncertain.append('surface_unknown')
    elif s < 8 or s > 400:
        uncertain.append(f'surface_outlier={s:g}')
    r = as_num(x.get('rooms'))
    if r is not None and (r < 1 or r > 12):
        uncertain.append(f'rooms_outlier={r:g}')
    if r is not None and s is not None and r >= 5 and s < 35:
        errors.append('rooms_surface_incoherent')
    if not (x.get('city') or x.get('location')):
        uncertain.append('location_too_weak')
    if not x.get('local_image_url'):
        uncertain.append('missing_local_primary_photo')
    if RE_TECH.search(blob_raw):
        errors.append('technical_fragment_visible_source')
    typ = norm(x.get('type'))
    if COMMERCIAL_HINTS.search(blob_raw) and not any(t in typ for t in HOUSING_TYPES):
        hide.append('commercial_or_non_housing_unclear')
    if x.get('furnished') is None:
        uncertain.append('furnished_unknown')
    return ('error' if errors else 'hide' if hide else 'uncertain' if uncertain else 'ok'), errors, uncertain + hide


def main() -> int:
    items = json.loads(LISTINGS.read_text()).get('listings') or []
    counts = Counter()
    samples = defaultdict(list)
    all_issues = []
    for x in items:
        status, errs, notes = classify(x)
        counts[status] += 1
        if status != 'ok' and len(samples[status]) < 25:
            samples[status].append({'id': x.get('id'), 'title': x.get('title'), 'price': x.get('price'), 'surface': x.get('surface'), 'rooms': x.get('rooms'), 'type': x.get('type'), 'issues': errs + notes})
        for issue in errs + notes:
            all_issues.append(issue.split('=')[0])

    report = {'ok': counts['error'] == 0, 'listing_count': len(items), 'status_counts': dict(counts), 'top_issues': Counter(all_issues).most_common(30), 'samples': dict(samples)}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if counts['error']:
        raise SystemExit(1)
    print('LISTING_SEMANTIC_TRUTH_AUDIT PASS')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
