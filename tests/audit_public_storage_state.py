#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

APP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/data/projects/reunion-immo-search/artifacts/app')
html = (APP / 'index.html').read_text(encoding='utf-8', errors='replace')
errors: list[str] = []

if 'localStorage' not in html and 'sessionStorage' not in html:
    errors.append('no client storage code found; audit may be pointed at wrong app')
if 'immo_clean_favs' not in html:
    errors.append('favorites storage key missing')
if 'immoHiddenIds' in html:
    errors.append('legacy immoHiddenIds storage key present on clean homepage')
if 'JSON.parse(localStorage.getItem' in html and 'try' not in html[max(0, html.find('JSON.parse(localStorage.getItem')-300):html.find('JSON.parse(localStorage.getItem')+300]:
    errors.append('localStorage JSON.parse appears unguarded by try/catch')
if 'history.replaceState' not in html and 'URLSearchParams' not in html:
    errors.append('shareable URL/search state hooks not found')
if not any(label in html for label in ['Tout effacer', 'Réinitialiser', 'Reinitialiser']):
    errors.append('clear/reset control missing')

# Bare homepage should not silently restore old hidden/search state from previous mobile sessions.
for forbidden in ['immo_search_state', 'immo_filters_state', 'immoHiddenIds']:
    if forbidden in html:
        errors.append(f'forbidden persistent homepage state key: {forbidden}')

# Search normalization should handle at least accents/hyphens enough for Réunion place names.
if not re.search(r'normalize\(|replace\(.{0,40}-', html, re.S):
    errors.append('search normalization/hyphen handling marker missing')

if errors:
    print('PUBLIC_STORAGE_STATE_AUDIT FAIL')
    for e in errors:
        print('-', e)
    raise SystemExit(1)
print('PUBLIC_STORAGE_STATE_AUDIT PASS')
