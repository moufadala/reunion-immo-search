#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path

APP=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/opt/data/projects/reunion-immo-search/artifacts/app')
errors=[]
cal=APP/'opportunity_calibration.json'
if not cal.exists() or cal.stat().st_size==0:
    errors.append('missing opportunity_calibration.json')
else:
    data=json.loads(cal.read_text(encoding='utf-8'))
    if data.get('version')!='opportunity_dedup_calibration_v1': errors.append('bad calibration version')
    if data.get('score_buckets',{}).get('strong_75_plus',0)<20: errors.append('too few strong opportunities')
    if data.get('dedup',{}).get('groups_count',0)<1: errors.append('dedup groups missing')
    if data.get('dedup',{}).get('annotated_seen_also_on',0)<20: errors.append('seen_also_on annotation too low')
    for token in ['opportunité forte','dédup non destructive','vu aussi sur']:
        if token not in ' '.join(data.get('contract') or []): errors.append(f'missing contract token {token}')
listings=json.loads((APP/'listings.json').read_text(encoding='utf-8')).get('listings') or []
if sum(1 for x in listings if 'seen_also_on' in x) != len(listings): errors.append('seen_also_on not on every listing')
if sum(1 for x in listings if x.get('seen_also_on')) < 20: errors.append('too few listings with seen_also_on')
html=(APP/'opportunites.html').read_text(encoding='utf-8', errors='replace')
for token in ['Calibration opportunités & doublons','Score expliqué','déduplication non destructive']:
    if token not in html: errors.append(f'opportunites.html missing {token}')
print(json.dumps({'ok':not errors,'errors':errors}, ensure_ascii=False, indent=2))
if errors: raise SystemExit(1)
print('OPPORTUNITY_DEDUP_CALIBRATION_AUDIT PASS')
