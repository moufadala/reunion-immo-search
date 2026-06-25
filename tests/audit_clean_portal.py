#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path('/opt/data/projects/reunion-immo-search')
APP = Path(__import__('os').environ.get('IMMO_APP_PATH', str(ROOT / 'artifacts/app')))
INDEX = APP / 'index.html'
LISTINGS = APP / 'listings.json'
COVERAGE = APP / 'coverage.json'

REQUIRED = [
    'Recherche immo RUN — portail propre',
    'Un portail immo simple, alimenté par notre base scrapée.',
    'minScore',
    'opportunity_score',
]

# Tokens that are expected in the technical workbench but must not leak into the family-facing homepage.
FORBIDDEN = [
    'Recherche immo Réunion — moteur visuel',
    'residential_status',
    'residential_reasons',
    'trust_flags',
    'duplicate_group_size',
    'missing_fields',
    'suspects.json',
    'source_health.html',
    'saved_searches.html',
    'alertSummary',
    'zoneMap',
    'renderZoneMap',
    'Recherches rapides famille',
    'Famille Nord ≤1200€',
    'T2/T3 ≤1000€',
    '2 chambres+',
    'data-preset',
    'Copier recherche',
    'copySearchBtn',
    'Lien copié',
]

FAMILY_URL_TOKENS = ['r', 'z', 't', 'rentMin', 'rentMax', 'bedroomsMin', 'minScore']


def fail(msg: str) -> None:
    print(f'CLEAN_PORTAL_AUDIT FAIL: {msg}', file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    if not INDEX.exists():
        fail(f'missing {INDEX}')
    if not LISTINGS.exists():
        fail(f'missing {LISTINGS}')
    if not COVERAGE.exists():
        fail(f'missing {COVERAGE}')

    html = INDEX.read_text(encoding='utf-8')
    for token in REQUIRED:
        if token not in html:
            fail(f'missing required clean token: {token}')
    leaked = [token for token in FORBIDDEN if token in html]
    if leaked:
        fail(f'forbidden technical token(s) in public index: {leaked}')
    for token in FAMILY_URL_TOKENS:
        if token not in html:
            fail(f'family alert URL param not supported in portal JS: {token}')

    listings = json.loads(LISTINGS.read_text(encoding='utf-8'))
    items = listings.get('listings') or []
    if len(items) < 400:
        fail(f'listing count too low: {len(items)}')
    if not all('opportunity_score' in x for x in items):
        fail('not every listing exposes opportunity_score in clean payload')
    local_paths = [x.get('local_image_url') for x in items if x.get('local_image_url')]
    if not local_paths:
        fail('no local image thumbs visible in clean payload')
    bad_abs = [p for p in local_paths if str(p).startswith('/thumbs/')]
    if bad_abs:
        fail(f'absolute local thumb paths are not portable: {bad_abs[:5]}')
    missing = [p for p in local_paths if str(p).startswith('thumbs/') and not (APP / str(p)).exists()]
    if missing:
        fail(f'local thumb file(s) missing: {missing[:5]}')
    if not all('description_analysis' in x for x in items):
        fail('not every listing exposes description_analysis semantic payload')

    coverage = json.loads(COVERAGE.read_text(encoding='utf-8'))
    if coverage.get('count') != len(items):
        fail(f'coverage count mismatch: {coverage.get("count")} != {len(items)}')

    title = re.search(r'<title>(.*?)</title>', html, re.S)
    print('CLEAN_PORTAL_AUDIT PASS', json.dumps({
        'title': title.group(1) if title else None,
        'listings': len(items),
        'local_photos': sum(1 for x in items if x.get('local_image_url')),
        'supports_family_url_params': FAMILY_URL_TOKENS,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
