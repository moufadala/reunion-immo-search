#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sonde : une page de DETAIL des 3 portails bloques passe-t-elle par le
navigateur reel du VPS (chromium-cdp) ?

Notre client HTTP prend 403 (seloger, zimo) et 503 (superimmo). Le scraping de
LISTES via CDP fonctionne deja (seloger_cdp_collect dans le pipeline quotidien).
La question ouverte est la page de detail.

Sortie : un verdict par URL + un HAR par portail, pour analyse hors-ligne.
Ne modifie aucune donnee.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(os.environ.get('PROBE_OUT', '/opt/data/artifacts/immo-cdp-probe'))


def cdp_url() -> str:
    host = os.environ.get('IMMO_CDP_HOST', 'chromium-cdp')
    port = os.environ.get('IMMO_CDP_PORT', '9223')
    try:
        host = socket.gethostbyname(host)
    except OSError:
        pass
    return 'http://%s:%s' % (host, port)


# marqueurs de blocage, pas de "page vide = bloque"
MARQUEURS = [
    'datadome', 'captcha-delivery', 'geo.captcha', 'pardon our interruption',
    'access denied', 'vous avez ete bloque', 'unusual traffic', 'incapsula',
    'attention required', 'cf-browser-verification',
]


def sonde(page, url: str) -> dict:
    r = {'url': url}
    t0 = time.time()
    try:
        rep = page.goto(url, wait_until='domcontentloaded', timeout=45000)
        r['status'] = rep.status if rep else None
    except Exception as e:                                   # noqa: BLE001
        r['erreur'] = '%s: %s' % (type(e).__name__, str(e)[:120])
        return r
    page.wait_for_timeout(2500)
    html = page.content()
    bas = html.lower()
    r['url_finale'] = page.url
    r['titre'] = (page.title() or '')[:110]
    r['html_octets'] = len(html)
    r['secondes'] = round(time.time() - t0, 1)
    r['blocage'] = [m for m in MARQUEURS if m in bas]

    # source structuree ?
    try:
        r['ldjson_types'] = page.evaluate(
            """() => [...document.querySelectorAll('script[type="application/ld+json"]')]
                 .map(s => { try { const j = JSON.parse(s.textContent);
                                   return Array.isArray(j) ? (j[0]||{})['@type'] : j['@type']; }
                             catch(e) { return 'parse_error'; } })""")
    except Exception:                                        # noqa: BLE001
        r['ldjson_types'] = []
    # signes qu'on a bien une annonce et pas une page d'erreur
    r['a_prix'] = ('€' in html or 'EUR' in html)
    r['nb_img'] = page.evaluate("() => document.images.length")
    return r


CIBLES = {
    'seloger': [
        'https://www.seloger.com/annonces/locations/appartement/saint-denis-974/275742581.htm',
        'https://www.seloger.com/annonces/locations/appartement/saint-denis-974/26VNHAZF13LN.htm',
    ],
    'zimo': [
        'https://www.zimo.fr/annonce/appartement-t3-59m2/019f9323-918a-7bde-9e03-6cffc8739a9a',
        'https://www.zimo.fr/annonce/appartement-4-pieces-bois-de-nefles/019f93c4-5c87-709a-b507-79784ab17cc1',
    ],
    'superimmo': [
        'https://www.superimmo.com/annonces/location-appartement-60m-saint-denis-97400-x11pdm2',
        'https://www.superimmo.com/annonces/location-maison-160m-sainte-clotilde-97490-x11r4o2',
    ],
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    resultats = {}
    with sync_playwright() as p:
        navigateur = p.chromium.connect_over_cdp(cdp_url())
        for portail, urls in CIBLES.items():
            har = OUT / ('%s.har' % portail)
            ctx = navigateur.new_context(
                locale='fr-FR', timezone_id='Indian/Reunion',
                viewport={'width': 1366, 'height': 900},
                record_har_path=str(har), record_har_content='omit')
            page = ctx.new_page()
            resultats[portail] = []
            for u in urls:
                r = sonde(page, u)
                resultats[portail].append(r)
                print('%-10s %-6s bloc=%-18s ld=%-28s img=%-3s %s'
                      % (portail, r.get('status'), ','.join(r.get('blocage') or []) or '-',
                         ','.join(str(x) for x in (r.get('ldjson_types') or [])) or '-',
                         r.get('nb_img'), (r.get('titre') or r.get('erreur') or '')[:60]))
                time.sleep(3)          # on ne martele pas
            ctx.close()                # ecrit le HAR
            print('   HAR -> %s (%s o)' % (har, har.stat().st_size if har.exists() else 0))
        navigateur.close()

    (OUT / 'verdict.json').write_text(
        json.dumps(resultats, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\nverdict -> %s' % (OUT / 'verdict.json'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
