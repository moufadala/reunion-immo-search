#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

APP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/data/projects/reunion-immo-search/artifacts/app')
errors: list[str] = []
robots = APP / 'robots.txt'
sitemap = APP / 'sitemap.xml'
if not robots.exists():
    errors.append('missing robots.txt')
else:
    text = robots.read_text(encoding='utf-8', errors='replace')
    if 'User-agent: *' not in text or 'Allow: /' not in text:
        errors.append('robots missing allow-all policy')
    if 'Sitemap: https://immo.148.230.103.174.sslip.io/sitemap.xml' not in text:
        errors.append('robots missing canonical sitemap URL')
if not sitemap.exists():
    errors.append('missing sitemap.xml')
else:
    text = sitemap.read_text(encoding='utf-8', errors='replace')
    if '/opt/data' in text or 'Traceback' in text:
        errors.append('technical leak in sitemap')
    try:
        root = ET.fromstring(text)
        ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        locs = [e.text or '' for e in root.findall('.//sm:loc', ns)]
    except Exception as exc:
        errors.append(f'invalid sitemap XML: {exc}')
        locs = []
    required = [
        'https://immo.148.230.103.174.sslip.io/',
        'https://immo.148.230.103.174.sslip.io/veille.html',
        'https://immo.148.230.103.174.sslip.io/sources.html',
        'https://immo.148.230.103.174.sslip.io/doublons.html',
        'https://immo.148.230.103.174.sslip.io/opportunites.html',
        'https://immo.148.230.103.174.sslip.io/localisation.html',
        'https://immo.148.230.103.174.sslip.io/alertes.html',
    ]
    missing = [u for u in required if u not in locs]
    if missing:
        errors.append(f'missing sitemap locs: {missing}')
if errors:
    raise SystemExit('PUBLIC_SEO_AUDIT FAIL ' + repr(errors))
print('PUBLIC_SEO_AUDIT PASS')
