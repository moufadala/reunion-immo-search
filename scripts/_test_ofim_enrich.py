#!/usr/bin/env python3
"""Manual OFIM detail-enrichment probe.

This helper is intentionally inert by default so broad audits never trigger a
live request. Set IMMO_ALLOW_LIVE_OFIM_PROBE=1 explicitly before running it.
"""
import os
import sys

if os.environ.get('IMMO_ALLOW_LIVE_OFIM_PROBE') != '1':
    print('SKIP live OFIM probe; set IMMO_ALLOW_LIVE_OFIM_PROBE=1 to run manually')
    raise SystemExit(0)

sys.path.insert(0, '/opt/data/scripts')
from ofim_scraper_v3 import enrich_detail  # noqa: E402

a = {
    'url': 'https://www.ofim.fr/73153/Location-Appartement-SAINTE-CLOTILDE-ile-de-la-Reunion-bel-appartement-meuble-situe-dans-la-Residence-Carrecube-1-chambre-balcon-vue-degagee.html',
    'prix': None,
}
enrich_detail(a)
print('resultat:', a)
