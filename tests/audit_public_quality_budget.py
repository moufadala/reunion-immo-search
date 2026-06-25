#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

APP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/data/projects/reunion-immo-search/artifacts/app')
MAX_LISTINGS_JSON_BYTES = 3_200_000
MAX_INDEX_BYTES = 120_000
MAX_PUBLIC_JSON_BYTES = 3_500_000
REQUIRED_PAGES = [
    'index.html', 'listings.json', 'veille.html', 'sources.html', 'doublons.html',
    'opportunites.html', 'localisation.html', 'alertes.html', 'changes.json',
    'source_health.json', 'dedup_groups.json', 'opportunity.json', 'locations.json', 'coverage.json',
]
SAFE_URL_PREFIXES = ('http://', 'https://', 'thumbs/', '#')
BAD_TEXT_TOKENS = ['Traceback', '/opt/data', 'sqlite3.OperationalError', 'SECRET_KEY', 'api_key=', 'password=']
EVENT_HANDLER_RE = re.compile(r'\son[a-z]+\s*=', re.I)

errors: list[str] = []
warnings: list[str] = []
for name in REQUIRED_PAGES:
    p = APP / name
    if not p.exists() or p.stat().st_size == 0:
        errors.append(f'missing_or_empty {name}')

index = APP / 'index.html'
if index.exists():
    size = index.stat().st_size
    if size > MAX_INDEX_BYTES:
        errors.append(f'index too large: {size}>{MAX_INDEX_BYTES}')
    html = index.read_text(encoding='utf-8', errors='replace')
    for token in BAD_TEXT_TOKENS:
        if token in html:
            errors.append(f'bad token in index: {token}')
    if 'name="viewport"' not in html and "name='viewport'" not in html:
        errors.append('missing viewport meta')
    if 'src="/thumbs/' in html or "src='/thumbs/" in html:
        errors.append('absolute /thumbs path in index')

listings_path = APP / 'listings.json'
if listings_path.exists():
    size = listings_path.stat().st_size
    if size > MAX_LISTINGS_JSON_BYTES:
        errors.append(f'listings.json too large: {size}>{MAX_LISTINGS_JSON_BYTES}')
    data = json.loads(listings_path.read_text(encoding='utf-8'))
    items = data.get('listings') or []
    if len(items) < 400:
        errors.append(f'too few listings: {len(items)}')
    dangerous_urls = []
    missing_local_files = []
    for item in items:
        for key in ['url', 'image_url', 'local_image_url', 'map_url']:
            value = item.get(key)
            if not value:
                continue
            value = str(value).strip()
            if not value.startswith(SAFE_URL_PREFIXES):
                dangerous_urls.append((item.get('id'), key, value[:120]))
            if key == 'local_image_url' and value.startswith('thumbs/') and not (APP / value).exists():
                missing_local_files.append((item.get('id'), value))
        for key in ['image_urls', 'local_image_urls']:
            values = item.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if not value:
                    continue
                value = str(value).strip()
                if not value.startswith(SAFE_URL_PREFIXES):
                    dangerous_urls.append((item.get('id'), key, value[:120]))
                if key == 'local_image_urls' and value.startswith('thumbs/') and not (APP / value).exists():
                    missing_local_files.append((item.get('id'), value))
    if dangerous_urls:
        errors.append(f'dangerous url protocols: {dangerous_urls[:5]}')
    if missing_local_files:
        errors.append(f'missing local image files: {missing_local_files[:5]}')

for p in APP.glob('*.json'):
    size = p.stat().st_size
    if size > MAX_PUBLIC_JSON_BYTES:
        warnings.append(f'large json {p.name}: {size}')

for p in APP.glob('*.html'):
    text = p.read_text(encoding='utf-8', errors='replace')
    for token in BAD_TEXT_TOKENS:
        if token in text:
            errors.append(f'bad token in {p.name}: {token}')
    # Inline onerror handlers are tolerated only in generated pages for image fallback;
    # they are tracked as warnings so we don't hide regression risk.
    if EVENT_HANDLER_RE.search(text):
        warnings.append(f'inline event handlers present in {p.name}')
    if 'src="/thumbs/' in text or "src='/thumbs/" in text:
        errors.append(f'absolute /thumbs path in {p.name}')

print(json.dumps({
    'ok': not errors,
    'app': str(APP),
    'errors': errors,
    'warnings': warnings[:20],
    'sizes': {
        'index_html': index.stat().st_size if index.exists() else None,
        'listings_json': listings_path.stat().st_size if listings_path.exists() else None,
    },
}, ensure_ascii=False, indent=2))
if errors:
    raise SystemExit(1)
print('PUBLIC_QUALITY_BUDGET_AUDIT PASS')
