# EXEMPLE PUBLIC. Le fichier reellement utilise est scripts/profils.py :
# il contient des donnees personnelles (budgets, quartiers, personnes) et
# reste LOCAL au VPS -- il est dans .gitignore, car ce depot est PUBLIC.
# Copier ce fichier vers scripts/profils.py et l'adapter.
# -*- coding: utf-8 -*-
"""Exemple de profils de recherche, et comment on classe une annonce.

Principe : AUCUN critere n'exclut. Tout est classe.
Un bien excellent dans une zone de rang 5 doit pouvoir passer devant un bien
mediocre en zone 1 -- donc la zone pese lourd mais ne verrouille rien.

Et un critere INCONNU ne penalise pas : on ne peut pas reprocher a une annonce
ce que le portail n'a pas publie. Il est juste signale comme inconnu.
"""

# rang de zone -> points. Ecart volontairement resserre entre rangs voisins
# pour qu'un ecart de qualite reelle puisse le compenser.
ZONE_POINTS = {1: 30, 2: 26, 3: 22, 4: 18, 5: 14, 6: 9}

ZONES_PROFIL_A = {
    1: ['Quartier prefere'],
    2: ['Quartier proche'],
    3: ['Quartier acceptable'],
    4: ['Quartier eloigne'],
}
ZONES_PROFIL_B = dict(ZONES_PROFIL_A)
ZONES_PROFIL_B[5] = ['Quartier elargi']

PROFILS = {
    'profil_a': {
        'nom': 'Profil A',
        'zones': ZONES_PROFIL_A,
        'surface_min': 50,
        'loyer_min': 500, 'loyer_max': 1000,
        'chambres_ideal': 2, 'chambres_min': 1,
        'types': None,
        'non_meuble_prefere': True,
        # accessibilite (plain-pied, ascenseur) prioritaire pour ce profil
        'bonus_acces': True,
    },
    'profil_b': {
        'nom': 'Profil B',
        'zones': ZONES_PROFIL_B,
        'surface_min': 90,
        'loyer_min': 800, 'loyer_max': 1500,
        'chambres_ideal': 3, 'chambres_min': 2,
        'types': ['maison', 'appartement', 'villa', 'house', 'flat', 'apartment', 'duplex'],
        'non_meuble_prefere': True,
        'bonus_acces': False,
    },
}


def _norm(s):
    import unicodedata
    import re
    if not s:
        return ''
    s = unicodedata.normalize('NFD', str(s)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()


def rang_zone(listing, zones):
    """Rang de la zone de l'annonce, ou None si hors des zones du profil."""
    blob = _norm('%s %s %s %s' % (listing.get('quartier') or '',
                                  listing.get('location_label') or '',
                                  listing.get('title') or '',
                                  listing.get('commune') or ''))
    for rang in sorted(zones):
        for z in zones[rang]:
            if _norm(z) and _norm(z) in blob:
                return rang, z
    return None, None


def scorer(listing, cle):
    """-> {score, rang_zone, zone, raisons[], alertes[]}  (score 0-100)"""
    p = PROFILS[cle]
    pts = 0.0
    raisons = []
    alertes = []

    # --- zone (30 max) ---
    rang, zone = rang_zone(listing, p['zones'])
    if rang:
        pts += ZONE_POINTS.get(rang, 8)
        if rang <= 2:
            raisons.append(zone)
    else:
        # hors zones nommees mais dans le perimetre : on ne jette pas
        pts += 4

    # --- loyer (22 max) ---
    r = listing.get('rent')
    if r is None:
        alertes.append('loyer non précisé')
        pts += 8
    elif p['loyer_min'] <= r <= p['loyer_max']:
        pts += 22
        # dans le budget ET dans le bas de la fourchette = mieux
        if r <= p['loyer_min'] + (p['loyer_max'] - p['loyer_min']) * 0.4:
            raisons.append('bon prix')
    elif r < p['loyer_min']:
        pts += 12  # suspect (bruit) mais pas exclu
        alertes.append('sous le budget mini')
    else:
        depassement = (r - p['loyer_max']) / p['loyer_max']
        pts += max(0, 16 - depassement * 60)
        alertes.append('au-dessus du budget')

    # --- surface (22 max) ---
    s = listing.get('surface')
    if s is None:
        alertes.append('surface non précisée')
        pts += 8
    elif s >= p['surface_min']:
        pts += 22
        if s >= p['surface_min'] * 1.3:
            raisons.append('%d m²' % round(s))
    else:
        manque = (p['surface_min'] - s) / p['surface_min']
        pts += max(0, 18 - manque * 55)
        alertes.append('plus petit que voulu')

    # --- chambres (16 max) ---
    ch = listing.get('bedrooms')
    deduit = False
    if ch is None and listing.get('rooms'):
        ch = max(listing['rooms'] - 1, 0)
        deduit = True
    if ch is None:
        pts += 6
        alertes.append('chambres inconnues')
    elif ch >= p['chambres_ideal']:
        pts += 16
        raisons.append('%d chambres' % ch)
    elif ch >= p['chambres_min']:
        pts += 11
    else:
        pts += 2
        alertes.append('trop peu de chambres')
    # NB : on n'alerte PAS quand les chambres sont deduites des pieces.
    # C'est le cas de la majorite des annonces -> l'alerte s'affichait sur
    # presque toutes les cartes et ne portait plus aucune information.
    # Le '~' accole au chiffre (webapp/src/components/Card.jsx) suffit.

    # --- type ---
    if p['types']:
        t = _norm(listing.get('type'))
        if t and not any(_norm(x) in t for x in p['types']):
            pts -= 12
            alertes.append('type inhabituel')

    # --- meuble (5 max) : prefere, jamais bloquant ---
    m = listing.get('furnished')
    if p['non_meuble_prefere']:
        if m == 0:
            pts += 5
            raisons.append('non meublé')
        elif m == 1:
            pts -= 3

    # --- acces : etage bas ou ascenseur (5 max) ---
    if p['bonus_acces']:
        f = (listing.get('floor') or '')
        if f.startswith('RDC'):
            pts += 5
            raisons.append('plain-pied')
        elif f.startswith('1'):
            pts += 4
            if listing.get('elevator') == 1:
                raisons.append('1er avec ascenseur')
        elif f and listing.get('elevator') == 1:
            pts += 3
        elif f and listing.get('elevator') == 0:
            pts -= 4
            alertes.append('étage sans ascenseur')

    # --- confort (10 max) ---
    confort = 0
    for k, v, mot in (('jardin', 3, 'jardin'), ('parking', 2, 'parking'),
                      ('terrasse', 2, 'terrasse'), ('veranda', 2, 'véranda'),
                      ('clim', 1, 'clim')):
        if listing.get(k):
            confort += v
            if v >= 2 and len(raisons) < 4:
                raisons.append(mot)
    if (listing.get('nb_sdb') or 0) >= 2:
        confort += 2
        raisons.append('%d salles de bain' % listing['nb_sdb'])
    pts += min(confort, 10)

    # --- fiabilite de la localisation : un bien mal localise est moins utile ---
    pts += min((listing.get('location_precision_rank') or 0) * 1.2, 6)

    return {
        'score': max(0, min(100, round(pts))),
        'rang_zone': rang,
        'zone': zone,
        'raisons': raisons[:4],
        'alertes': alertes[:3],
    }
