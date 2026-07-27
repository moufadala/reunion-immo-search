#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exporte le feed du produit immo : rental_listings + enrichment + detail.

Un seul fichier de sortie, un seul contrat. L'interface ne lit QUE ca.
Remplace la chaine de patchs qui injectaient du HTML dans index.html.

Sortie : artifacts/app/feed.json
  {meta: {...}, listings: [...], movements: {...}, sources: [...]}
"""
from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from profils import PROFILS, scorer  # noqa: E402
import geo_quartiers as gq  # noqa: E402

DB = os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db')
ROOT = '/opt/data/projects/reunion-immo-search'
OUT = os.environ.get('IMMO_FEED_OUT', ROOT + '/artifacts/app/feed.json')
# distances precalculees quartier -> point de reference.
# Le point de reference lui-meme reste dans ce fichier COTE SERVEUR et n'est
# jamais recopie dans le feed servi : seules les durees en sortent.
DIST = ROOT + '/config/distances_quartiers.json'

COMMUNES = ['Saint-Denis', 'Sainte-Marie', 'Sainte-Suzanne', 'Saint-André']

# ordre de confiance, du plus precis au moins precis
PRECISION_RANK = {
    'adresse_exacte': 5,
    'rue': 4,
    'residence': 3,
    'point_carte': 3,
    'quartier': 2,
    'commune': 1,
    'inconnu': 0,
}
PRECISION_LABEL = {
    'adresse_exacte': 'Adresse exacte',
    'rue': 'Rue',
    'residence': 'Résidence',
    'point_carte': 'Point cartographique',
    'quartier': 'Quartier',
    'commune': 'Commune seule',
    'inconnu': 'Localisation inconnue',
}


def norm(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFD', str(s)).encode('ascii', 'ignore').decode()
    s = s.lower().replace(chr(39), ' ')
    s = re.sub(r'\bst\b', 'saint', s)
    s = re.sub(r'\bste\b', 'sainte', s)
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def commune_of(city_norm, enrich_city):
    for c in COMMUNES:
        if norm(enrich_city) == norm(c) or city_norm == norm(c):
            return c
    # Avant : on renvoyait enrich_city tel quel en dernier repli, ce qui
    # affichait des communes hors perimetre (ex. "Plaine Des Cafres") comme
    # si elles etaient valides. On tente d'abord un signal fiable (alias
    # commune, code postal) via geo_quartiers ; sinon on dit franchement
    # qu'on ne sait pas plutot que d'inventer une commune.
    return None


# Valeurs qui ne veulent rien dire : elles ne doivent pas devenir un libelle.
# NB : norm() remplace les separateurs par des TIRETS -- ces cles doivent donc
# etre ecrites avec des tirets, sinon la comparaison echoue silencieusement.
VIDES = {'zone-non-precisee', 'non-precisee', 'non-precise', 'region-non-precisee',
         'non-renseigne', 'autre', 'inconnu', 'na', ''}


def quartier_propre(brut, commune):
    """Les portails stockent le quartier sous la forme
    'Saint-Denis - Providence - Les Camelias'. On ne garde que la partie
    QUARTIER (la commune est deja affichee a cote), et on jette les
    valeurs vides deguisees en libelle."""
    if not brut:
        return None
    s = str(brut).strip()
    if norm(s) in VIDES:
        return None
    if ' - ' in s:
        bouts = [b.strip() for b in s.split(' - ') if b.strip()]
        # on retire les bouts qui sont la commune elle-meme
        bouts = [b for b in bouts if norm(b) != norm(commune)]
        if not bouts:
            return None
        # le dernier bout est le plus precis, mais on garde au plus 2 niveaux
        s = ' · '.join(bouts[-2:]) if len(bouts) > 1 else bouts[0]
    if norm(s) == norm(commune) or norm(s) in VIDES:
        return None
    return s


def main():
    c = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    c.row_factory = sqlite3.Row

    detail = {}
    try:
        for r in c.execute('select * from listing_detail'):
            detail[(r['source_site'], r['source_id'])] = dict(r)
    except sqlite3.OperationalError:
        pass

    # --- PHOTOS : uniquement nos copies locales. Decision Moufadal 27/07 :
    # l'app ne doit JAMAIS dependre d'un lien externe. Preuve du besoin : 35
    # photos zimo renvoient desormais un placeholder de 266 o a la source.
    # Le manifeste est produit par scripts/cache_photos.py (idempotent).
    # Les chemins sont ABSOLUS : l'app est servie depuis /v2/ aujourd'hui et
    # depuis / demain -- un chemin relatif casserait a la bascule.
    thumbs = {}
    try:
        man = json.load(open(ROOT + '/artifacts/app/photos_manifest.json', encoding='utf-8'))
        for cle, v in (man.get('photos') or {}).items():
            site, _, sid = cle.partition(':')
            loc = v.get('local')
            if loc and os.path.exists(os.path.join(ROOT, 'artifacts/app', loc.lstrip('/'))):
                thumbs[(site, sid)] = loc
    except (OSError, ValueError):
        pass
    # Filet : anciennes vignettes referencees par le pipeline precedent et pas
    # encore reprises par le manifeste. On ne perd rien de ce qui existe deja.
    try:
        old = json.load(open(ROOT + '/artifacts/app/listings.json', encoding='utf-8'))
        items = old if isinstance(old, list) else (old.get('items') or old.get('listings') or [])
        for it in items:
            k2 = (it.get('source'), str(it.get('source_id')))
            u = it.get('local_image_url')
            if k2 in thumbs or not u:
                continue
            if os.path.exists(os.path.join(ROOT, 'artifacts/app', u)):
                thumbs[k2] = '/' + u.lstrip('/')
    except (OSError, ValueError):
        pass

    dist = {}
    try:
        with open(DIST, encoding='utf-8') as f:
            dist = json.load(f).get('quartiers', {})
    except (OSError, ValueError):
        pass
    dist_norm = {norm(k): v for k, v in dist.items()}

    def trajet(l):
        """Temps de trajet voiture. On dit TOUJOURS si c'est mesure ou estime."""
        if l.get('lat') and l.get('lon'):
            # coordonnees reelles : on approche par le quartier le plus proche
            best, bd = None, 1e9
            for k, v in dist.items():
                d = ((v['lat'] - l['lat']) ** 2 + (v['lon'] - l['lon']) ** 2) ** 0.5
                if d < bd:
                    best, bd = v, d
            if best and bd < 0.045:  # ~5 km
                return {'minutes': best['minutes'], 'km': best['km'], 'estime': False}
        # Les libelles nettoyes peuvent etre composes ("Bois de Nefles · Sainte-Clotilde")
        # et la commune seule n'est pas une cle de la table (qui contient
        # "Saint-Denis centre"). On essaie donc chaque morceau, puis la commune,
        # puis "<commune> centre".
        essais = []
        for champ in ('quartier', 'location_label'):
            v = l.get(champ)
            if v:
                essais.extend(m.strip() for m in str(v).split('·'))
                essais.append(v)
        if l.get('commune'):
            essais.append(l['commune'])
            essais.append('%s centre' % l['commune'])
        for e in essais:
            v = dist_norm.get(norm(e))
            if v:
                return {'minutes': v['minutes'], 'km': v['km'], 'estime': True}
        return None

    enrich = {}
    for r in c.execute('select * from listing_product_enrichment'):
        enrich[(r['source_site'], r['source_id'])] = dict(r)

    now = datetime.now(timezone.utc)
    d7 = (now - timedelta(days=7)).isoformat()
    d30 = (now - timedelta(days=30)).isoformat()

    listings = []
    for r in c.execute('select * from rental_listings order by seen_last_at desc'):
        k = (r['source_site'], r['source_id'])
        e = enrich.get(k, {})
        d = detail.get(k, {})

        lat, lon = d.get('lat'), d.get('lon')
        commune = commune_of(norm(r['city']), e.get('city_normalized'))
        if not commune:
            # Repli 27/07 : la commune declaree par le portail est parfois
            # vide/mal formee (code postal seul, "individuel Saint-Denis",
            # nom de quartier sans la commune...). On cherche un signal
            # commune non ambigu dans les champs bruts + le texte de
            # l'annonce avant d'abandonner.
            commune = gq.infer_commune(
                r['city'], r['district'], e.get('city_normalized'),
                e.get('zone_normalized'), r['title'])
        quartier = (quartier_propre(e.get('zone_normalized'), commune)
                    or quartier_propre(r['district'], commune)
                    or gq.infer_quartier(
                        commune, r['title'],
                        d.get('description_full') or r['description'],
                        r['district'], e.get('zone_normalized')))

        # --- localisation : on prend le SIGNAL LE PLUS PRECIS disponible, et on
        # dit toujours d'ou il vient. Jamais d'invention.
        prec = d.get('precision') or 'inconnu'
        label = None
        if d.get('address'):
            label = d['address']
        elif d.get('street'):
            label = d['street']
        elif d.get('residence'):
            label = 'Résidence %s' % d['residence']
        elif quartier:
            label = quartier
            prec = 'quartier'
        elif commune and norm(commune) not in VIDES:
            label = commune
            prec = 'commune'
        else:
            label = None
            prec = 'inconnu'

        listings.append({
            'id': '%s:%s' % (r['source_site'], r['source_id']),
            'source': r['source_site'],
            'url': r['canonical_url'] or r['url'],
            'title': r['title'],
            'active': bool(r['is_active']),
            'seen_first': r['seen_first_at'],
            'seen_last': r['seen_last_at'],
            'published': r['published_at'],
            # prix / surface
            'rent': r['rent_eur'],
            'charges': d.get('charges_eur') if d.get('charges_eur') is not None else r['charges_eur'],
            'surface': r['surface_m2'],
            'rooms': r['rooms'],
            'bedrooms': d.get('bedrooms') if d.get('bedrooms') is not None else r['bedrooms'],
            'type': e.get('property_type_normalized') or r['property_type'],
            'residential': bool(e.get('is_residential', 1)),
            'canonical': bool(e.get('is_canonical', 1)),
            'agency': r['agency_or_owner'],
            # localisation — le coeur de la demande du 27/07
            'commune': commune if commune and norm(commune) not in VIDES else None,
            'quartier': quartier,
            'location_label': label,
            'location_precision': prec,
            'location_precision_rank': PRECISION_RANK.get(prec, 0),
            'location_precision_label': PRECISION_LABEL.get(prec, prec),
            'location_source': d.get('geo_source'),
            'lat': lat,
            'lon': lon,
            'address': d.get('address'),
            'street': d.get('street'),
            'residence': d.get('residence'),
            # criteres fins, enfin disponibles
            'floor': d.get('floor'),
            'elevator': d.get('has_elevator'),
            'bathtub': d.get('bathtub'),
            'furnished': d.get('furnished'),
            # confort interieur (demande du 27/07)
            'nb_sdb': d.get('nb_sdb'), 'nb_wc': d.get('nb_wc'),
            'wc_separe': d.get('wc_separe'), 'niveaux': d.get('niveaux'),
            'jardin': d.get('jardin'), 'veranda': d.get('veranda'),
            'terrasse': d.get('terrasse'), 'parking': d.get('parking'),
            'piscine': d.get('piscine'), 'clim': d.get('clim'),
            # media / texte — UNIQUEMENT notre copie locale. Pas de repli sur
            # l'URL distante : un lien qui meurt chez le portail (cas zimo) ne
            # doit plus jamais casser une carte.
            'image': thumbs.get((r['source_site'], str(r['source_id']))),
            'image_locale': (r['source_site'], str(r['source_id'])) in thumbs,
            'description': d.get('description_full') or r['description'],
            'detail_read': bool(d.get('http_status') == 200),
        })

    # --- trajet + score par profil ---
    for l in listings:
        l['trajet'] = trajet(l)
        l['profils'] = {}
        for cle in PROFILS:
            s = scorer(l, cle)
            # on ne rattache l'annonce a un profil que si elle a un sens pour lui
            if s['score'] >= 45:
                l['profils'][cle] = s
        l['meilleur_profil'] = (
            max(l['profils'].items(), key=lambda kv: kv[1]['score'])[0]
            if l['profils'] else None)
        l['meilleur_score'] = (
            l['profils'][l['meilleur_profil']]['score'] if l['meilleur_profil'] else 0)
        j = (datetime.fromisoformat(l['seen_first']).astimezone(timezone.utc)
             if l['seen_first'] else None)
        l['fraiche'] = bool(j and (now - j).days <= 3)

    # --- mouvements : ce que Moufadal veut voir sans se prendre la tete
    new7 = [x for x in listings if (x['seen_first'] or '') >= d7]
    gone = [x for x in listings if not x['active']]
    gone7 = [x for x in gone if (x['seen_last'] or '') >= d7]

    movements = {
        'nouvelles_7j': len(new7),
        'disparues_total': len(gone),
        'disparues_7j': len(gone7),
        'actives': sum(1 for x in listings if x['active']),
    }

    # --- sante des sources
    per_source = defaultdict(lambda: {'total': 0, 'actives': 0, 'detail_lu': 0, 'dernier': ''})
    for x in listings:
        s = per_source[x['source']]
        s['total'] += 1
        s['actives'] += 1 if x['active'] else 0
        s['detail_lu'] += 1 if x['detail_read'] else 0
        if (x['seen_last'] or '') > s['dernier']:
            s['dernier'] = x['seen_last']
    sources = [dict(nom=k, **v) for k, v in sorted(per_source.items())]

    prec_counts = Counter(x['location_precision'] for x in listings)
    meta = {
        'genere_le': now.isoformat(),
        'perimetre': COMMUNES,
        'total': len(listings),
        'actives': movements['actives'],
        'avec_point_carte': sum(1 for x in listings if x['lat']),
        'avec_trajet': sum(1 for x in listings if x['trajet']),
        'avec_photo_locale': sum(1 for x in listings if x['image']),
        'actives_sans_photo': sum(1 for x in listings if x['active'] and not x['image']),
        'detail_lu': sum(1 for x in listings if x['detail_read']),
        'fraiches': sum(1 for x in listings if x['fraiche'] and x['active']),
        'precision': {PRECISION_LABEL.get(k, k): v for k, v in prec_counts.most_common()},
        'profils': {
            k: {'nom': v['nom'],
                'resume': '%d–%d € · ≥ %d m² · %d ch. min'
                          % (v['loyer_min'], v['loyer_max'], v['surface_min'],
                             v['chambres_min']),
                'correspondances': sum(1 for x in listings
                                       if x['active'] and k in x['profils'])}
            for k, v in PROFILS.items()
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'listings': listings, 'movements': movements,
                   'sources': sources}, f, ensure_ascii=False, separators=(',', ':'))

    print('feed ecrit : %s (%.2f Mo)' % (OUT, os.path.getsize(OUT) / 1e6))
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print('mouvements :', json.dumps(movements, ensure_ascii=False))


if __name__ == '__main__':
    main()
