#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sonde 2 : les portails encore bloques passent-ils avec l'egress RESIDENTIEL ?

Sonde 1 (probe_detail_cdp.py) a montre : seloger passe deja par le navigateur du
VPS ; zimo (403 "Un instant...") et superimmo (503 "Prouvez que vous etes un
humain") non. Les MEMES URL s'ouvrent depuis le Chrome du PC. La variable isolee
est donc l'IP, pas le navigateur.

Ici on relance le meme navigateur, une seule chose change : la sortie reseau.

Piege du skill web-scraping-reality : ALL_PROXY ne suffit pas pour Chromium.
Le proxy DOIT etre passe a launch(proxy=...), sinon faux negatif.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(os.environ.get('PROBE_OUT', '/opt/data/artifacts/immo-cdp-probe'))
PROXY = os.environ.get('IMMO_PROXY', 'socks5://127.0.0.1:1055')

MARQUEURS = ['datadome', 'captcha-delivery', 'un instant', 'prouvez que vous',
             'access denied', 'attention required', 'just a moment']

CIBLES = {
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
    res = {'proxy': PROXY, 'portails': {}}
    with sync_playwright() as p:
        nav = p.chromium.launch(
            headless=True,
            proxy={'server': PROXY},
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        # preuve de l'IP reellement utilisee PAR LE NAVIGATEUR (pas par curl)
        ctx0 = nav.new_context()
        pg0 = ctx0.new_page()
        try:
            pg0.goto('https://ifconfig.co/json', timeout=40000)
            res['ip_navigateur'] = json.loads(pg0.inner_text('pre'))
            print('IP vue par le navigateur : %s (%s, %s)'
                  % (res['ip_navigateur'].get('ip'), res['ip_navigateur'].get('asn_org'),
                     res['ip_navigateur'].get('country')))
        except Exception as e:                               # noqa: BLE001
            print('preflight IP echoue: %s' % e)
            res['ip_navigateur'] = {'erreur': str(e)[:150]}
        ctx0.close()

        for portail, urls in CIBLES.items():
            har = OUT / ('%s-residentiel.har' % portail)
            ctx = nav.new_context(locale='fr-FR', timezone_id='Indian/Reunion',
                                  viewport={'width': 1366, 'height': 900},
                                  record_har_path=str(har), record_har_content='omit')
            page = ctx.new_page()
            res['portails'][portail] = []
            for u in urls:
                r = {'url': u}
                try:
                    rep = page.goto(u, wait_until='domcontentloaded', timeout=50000)
                    r['status'] = rep.status if rep else None
                    page.wait_for_timeout(3000)
                    html = page.content()
                    r['titre'] = (page.title() or '')[:110]
                    r['html_octets'] = len(html)
                    r['blocage'] = [m for m in MARQUEURS if m in html.lower()]
                    r['nb_img'] = page.evaluate('() => document.images.length')
                    r['ldjson'] = page.evaluate(
                        """() => [...document.querySelectorAll('script[type="application/ld+json"]')]
                             .map(s => { try { const j = JSON.parse(s.textContent);
                                               return Array.isArray(j) ? (j[0]||{})['@type'] : j['@type']; }
                                         catch(e) { return 'parse_error'; } })""")
                except Exception as e:                       # noqa: BLE001
                    r['erreur'] = '%s: %s' % (type(e).__name__, str(e)[:120])
                res['portails'][portail].append(r)
                print('%-10s %-5s bloc=%-16s img=%-3s ld=%-22s %s'
                      % (portail, r.get('status'), ','.join(r.get('blocage') or []) or '-',
                         r.get('nb_img'), ','.join(str(x) for x in (r.get('ldjson') or [])) or '-',
                         (r.get('titre') or r.get('erreur') or '')[:55]))
                time.sleep(4)
            ctx.close()
            print('   HAR -> %s (%s o)' % (har, har.stat().st_size if har.exists() else 0))
        nav.close()

    (OUT / 'verdict-residentiel.json').write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\nverdict -> %s' % (OUT / 'verdict-residentiel.json'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
