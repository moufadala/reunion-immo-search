#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
import os

APP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/data/projects/reunion-immo-search/artifacts/app')
errors=[]; warnings=[]
listings=APP/'listings.json'; photo=APP/'photo_quality.json'; coverage=APP/'coverage.json'
for p in [listings, photo, coverage]:
    if not p.exists() or p.stat().st_size == 0:
        errors.append(f'missing_or_empty {p.name}')
items=[]
if listings.exists():
    data=json.loads(listings.read_text(encoding='utf-8'))
    items=data.get('listings') or []
    if len(items) < 400: errors.append(f'too few listings: {len(items)}')
    with_valid=sum(1 for x in items if (x.get('image_quality') or {}).get('valid_local_count'))
    with_primary=sum(1 for x in items if x.get('local_image_url'))
    with_gallery=sum(1 for x in items if len(x.get('local_image_urls') or []) > 1)
    if with_primary < 350: errors.append(f'local primary image coverage too low: {with_primary}')
    if with_valid < 350: errors.append(f'valid local image coverage too low: {with_valid}')
    if with_gallery < 100: warnings.append(f'multi-photo gallery coverage modest: {with_gallery}')
    geo_known=sum(1 for x in items if (x.get('geo_quality') or {}).get('level') in {'haute','moyenne','commune'})
    if geo_known < 400: errors.append(f'geo confidence coverage too low: {geo_known}')
    missing_files=[]
    for x in items:
        for rel in x.get('local_image_urls') or []:
            if str(rel).startswith('thumbs/') and not (APP/str(rel)).exists():
                missing_files.append((x.get('id'), rel))
                if len(missing_files) >= 5: break
        if len(missing_files) >= 5: break
    if missing_files:
        msg = f'missing local gallery files: {missing_files}'
        if os.environ.get('IMMO_ALLOW_MISSING_MEDIA') == '1':
            warnings.append(msg)
        else:
            errors.append(msg)
if photo.exists():
    pdata=json.loads(photo.read_text(encoding='utf-8'))
    if pdata.get('version') != 'photo_quality_v1': errors.append('photo_quality version mismatch')
    summary=pdata.get('summary') or {}
    if summary.get('with_valid_local',0) < 350: errors.append(f'photo_quality valid count too low: {summary}')
if coverage.exists():
    cov=json.loads(coverage.read_text(encoding='utf-8'))
    for key in ['valid_local_images','photo_quality_issues','geo_confidence','amenity_tags']:
        if key not in cov: errors.append(f'coverage missing {key}')
print(json.dumps({'ok':not errors,'app':str(APP),'errors':errors,'warnings':warnings[:20],'metrics':{'listings':len(items),'valid_images':sum(1 for x in items if (x.get('image_quality') or {}).get('valid_local_count')) if items else 0,'geo_known':sum(1 for x in items if (x.get('geo_quality') or {}).get('level') in {'haute','moyenne','commune'}) if items else 0}}, ensure_ascii=False, indent=2))
if errors: raise SystemExit(1)
print('GEO_PHOTO_QUALITY_AUDIT PASS')
