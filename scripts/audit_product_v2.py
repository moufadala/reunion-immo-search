#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Porte de QA du produit v2. Echoue le run si une regle posee a regresse.

C'est la commande de QA du chantier "photos chez nous" : la relancer suffit a
prouver que la regle tient encore.

    python3 scripts/audit_product_v2.py artifacts/app
"""
from __future__ import annotations

import json
import pathlib
import sys

MAX_ACTIVES_SANS_PHOTO = int(__import__('os').environ.get('IMMO_MAX_ACTIVES_SANS_PHOTO', '30'))


def main() -> int:
    app = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'artifacts/app')
    feed_p = app / 'feed.json'
    if not feed_p.is_file():
        print('ECHEC: %s absent' % feed_p, file=sys.stderr)
        return 1
    feed = json.loads(feed_p.read_text(encoding='utf-8'))
    listings = feed['listings']
    erreurs = []

    # 1. Regle Moufadal 27/07 : plus jamais de dependance a un lien externe.
    distantes = [x['id'] for x in listings
                 if x.get('image') and not str(x['image']).startswith('/thumbs/')]
    if distantes:
        erreurs.append('%d annonces pointent vers une image DISTANTE (%s...)'
                       % (len(distantes), ', '.join(distantes[:3])))

    # 2. Les vignettes referencees existent vraiment sur le disque servi.
    cassees = [x['id'] for x in listings
               if x.get('image') and not (app / str(x['image']).lstrip('/')).is_file()]
    if cassees:
        erreurs.append('%d vignettes referencees mais absentes du disque (%s...)'
                       % (len(cassees), ', '.join(cassees[:3])))

    # 3. L'interface est deployee.
    for f in ('v2/index.html', 'v2/feed.json'):
        if not (app / f).exists():
            erreurs.append('manque %s' % f)

    # 4. Le feed ne doit pas contenir le point de reference prive.
    brut = feed_p.read_text(encoding='utf-8')
    for interdit in ('point_reference', 'reference_lat', 'reference_lon'):
        if interdit in brut:
            erreurs.append('le feed contient %s (donnee privee)' % interdit)

    actives = [x for x in listings if x['active']]
    sans = [x for x in actives if not x.get('image')]
    if len(sans) > MAX_ACTIVES_SANS_PHOTO:
        erreurs.append('%d annonces actives sans photo (plafond %d)'
                       % (len(sans), MAX_ACTIVES_SANS_PHOTO))

    resultat = {
        'ok': not erreurs,
        'annonces': len(listings),
        'actives': len(actives),
        'avec_photo_locale': sum(1 for x in listings if x.get('image')),
        'actives_sans_photo': len(sans),
        'erreurs': erreurs,
    }
    print(json.dumps(resultat, ensure_ascii=False))
    if erreurs:
        for e in erreurs:
            print('ECHEC: %s' % e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
