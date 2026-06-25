#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

APP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/data/projects/reunion-immo-search/artifacts/app')
full = APP / 'listings.json'
index = APP / 'listings_index.json'
errors: list[str] = []
if not full.exists():
    errors.append('missing listings.json')
if not index.exists():
    errors.append('missing listings_index.json')
if errors:
    raise SystemExit('PUBLIC_PERF_INDEX_AUDIT FAIL ' + json.dumps(errors, ensure_ascii=False))
full_bytes = full.stat().st_size
index_bytes = index.stat().st_size
full_gz = len(gzip.compress(full.read_bytes()))
index_gz = len(gzip.compress(index.read_bytes()))
full_data = json.loads(full.read_text(encoding='utf-8'))
index_data = json.loads(index.read_text(encoding='utf-8'))
full_count = len(full_data.get('listings') or [])
index_count = len(index_data.get('listings') or [])
if full_count != index_count:
    errors.append(f'count mismatch full={full_count} index={index_count}')
if index_bytes >= full_bytes * 0.55:
    errors.append(f'index too large: {index_bytes}/{full_bytes}')
if index_gz >= full_gz * 0.75:
    errors.append(f'index gzip too large: {index_gz}/{full_gz}')
for item in (index_data.get('listings') or [])[:20]:
    if 'description' in item or 'description_analysis' in item or 'image_urls' in item:
        errors.append('index includes heavy fields')
        break
report = {
    'app': str(APP),
    'count': full_count,
    'full_bytes': full_bytes,
    'index_bytes': index_bytes,
    'full_gzip_bytes': full_gz,
    'index_gzip_bytes': index_gz,
    'index_ratio': round(index_bytes / full_bytes, 3) if full_bytes else None,
    'index_gzip_ratio': round(index_gz / full_gz, 3) if full_gz else None,
}
if errors:
    raise SystemExit('PUBLIC_PERF_INDEX_AUDIT FAIL ' + json.dumps({'errors': errors, **report}, ensure_ascii=False))
print('PUBLIC_PERF_INDEX_AUDIT PASS ' + json.dumps(report, ensure_ascii=False))
