#!/usr/bin/env python3
from __future__ import annotations
import json, re, sys
from pathlib import Path

APP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/data/projects/reunion-immo-search/artifacts/app')
errors=[]
EVENT_HANDLER_RE=re.compile(r'\son[a-z]+\s*=', re.I)
for name in ['changes.html','veille.html']:
    p=APP/name
    if not p.exists() or p.stat().st_size == 0:
        errors.append(f'missing_or_empty {name}')
        continue
    text=p.read_text(encoding='utf-8', errors='replace')
    if EVENT_HANDLER_RE.search(text):
        errors.append(f'inline handler remained in {name}')
    if '<script' not in text:
        errors.append(f'{name} missing script; filtering page may be broken')
    if 'Content-Security-Policy' in text and "script-src 'unsafe-inline'" in text:
        errors.append(f'{name} CSP allows unsafe-inline scripts')
    for bad in ['/opt/data','Traceback','sqlite3.OperationalError','SECRET_KEY','api_key=','password=']:
        if bad in text:
            errors.append(f'{name} leaks {bad}')
print(json.dumps({'ok':not errors,'app':str(APP),'errors':errors}, ensure_ascii=False, indent=2))
if errors:
    raise SystemExit(1)
print('INLINE_CSP_SIDEPAGES_AUDIT PASS')
