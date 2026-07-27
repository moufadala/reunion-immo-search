#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Precalcule la distance ROUTIERE (voiture) entre chaque quartier cible et le
point de reference de Moufadal.

Pourquoi precalculer : on n'a les coordonnees exactes que pour ~14 % des annonces.
Pour les autres on ne peut donner qu'une estimation depuis le centre du quartier.
Autant la calculer UNE fois (25 quartiers) et la mettre en cache, plutot que
d'appeler un service de routage a chaque affichage.

A vol d'oiseau serait faux a La Reunion : ravines, routes en lacets, littoral.
On passe donc par OSRM (routage voiture reel sur le reseau OpenStreetMap).

Sortie : config/distances_quartiers.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

UA = 'immo-nord-est/1.0 (usage prive, contact via github moufadala)'
NOMINATIM = 'https://nominatim.openstreetmap.org/search'
OSRM = 'https://router.project-osrm.org/route/v1/driving'

# Point de reference. Volontairement garde COTE SERVEUR : il ne part jamais
# dans le feed servi. Seules les distances calculees y figurent.
# Point de reference : JAMAIS dans le code. Ce depot GitHub est PUBLIC
# (verifie le 2026-07-27), et une coordonnee au 7e decimale designe une
# adresse. Elle vit dans config/reference_point.json, qui est gitignore.
# Modele : config/reference_point.example.json
_REF_FILE = os.environ.get('IMMO_REFERENCE_POINT',
                          os.path.join(os.path.dirname(os.path.dirname(
                              os.path.abspath(__file__))),
                              'config', 'reference_point.json'))
try:
    with open(_REF_FILE, encoding='utf-8') as _f:
        REF = json.load(_f)
except (OSError, ValueError) as _e:
    raise SystemExit(
        'point de reference absent : %s\n'
        'copier config/reference_point.example.json et y mettre vos coordonnees.'
        % _REF_FILE)

QUARTIERS = [
    ('Duparc', 'Sainte-Marie'),
    ('Beauséjour', 'Sainte-Marie'),
    ('La Convenance', 'Sainte-Marie'),
    ('Les Cafés', 'Sainte-Marie'),
    ('Rivière des Pluies', 'Sainte-Marie'),
    ('La Ressource', 'Sainte-Marie'),
    ('Sainte-Marie centre', 'Sainte-Marie'),
    ('La Bretagne', 'Saint-Denis'),
    ('Sainte-Clotilde', 'Saint-Denis'),
    ('Le Chaudron', 'Saint-Denis'),
    ('Moufia', 'Saint-Denis'),
    ('Bellepierre', 'Saint-Denis'),
    ('Montgaillard', 'Saint-Denis'),
    ('La Montagne', 'Saint-Denis'),
    ('Saint-François', 'Saint-Denis'),
    ('Bois de Nèfles', 'Saint-Denis'),
    ('Sainte-Clotilde centre', 'Saint-Denis'),
    ('Saint-Denis centre', 'Saint-Denis'),
    ('Bagatelle', 'Sainte-Suzanne'),
    ('Sainte-Suzanne centre', 'Sainte-Suzanne'),
    ('Quartier Français', 'Sainte-Suzanne'),
    ('Saint-André centre', 'Saint-André'),
    ('Cambuston', 'Saint-André'),
    ('Champ-Borne', 'Saint-André'),
    ('Rivière du Mât', 'Saint-André'),
]


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def geocode(quartier, commune):
    q = '%s, %s, La Réunion' % (quartier, commune)
    # PROUVE 2026-07-27 : countrycodes='re' renvoie 0 resultat pour La Reunion
    # (elle est indexee sous 'fr'). Ne pas remettre ce filtre.
    url = '%s?%s' % (NOMINATIM, urllib.parse.urlencode(
        {'q': q, 'format': 'json', 'limit': 1}))
    try:
        r = get(url)
    except Exception as e:
        print('  geocode KO %-24s %s' % (quartier, e))
        return None
    if not r:
        print('  geocode VIDE %s' % quartier)
        return None
    lat, lon = float(r[0]['lat']), float(r[0]['lon'])
    if not (-21.45 < lat < -20.80 and 55.15 < lon < 55.95):
        print('  geocode HORS ILE %s -> %s,%s' % (quartier, lat, lon))
        return None
    return lat, lon


def route(lat, lon):
    """Distance et duree voiture depuis le point de reference."""
    url = '%s/%s,%s;%s,%s?overview=false' % (OSRM, REF['lon'], REF['lat'], lon, lat)
    try:
        r = get(url)
    except Exception as e:
        print('    routage KO : %s' % e)
        return None
    if r.get('code') != 'Ok' or not r.get('routes'):
        return None
    x = r['routes'][0]
    return round(x['distance'] / 1000, 1), int(round(x['duration'] / 60))


def main():
    out = {'reference': {'label': REF['label']}, 'quartiers': {}}
    for quartier, commune in QUARTIERS:
        g = geocode(quartier, commune)
        time.sleep(1.1)  # politesse Nominatim : 1 req/s max
        if not g:
            continue
        lat, lon = g
        r = route(lat, lon)
        time.sleep(0.4)
        if not r:
            print('  %-26s geocode OK mais pas de route' % quartier)
            continue
        km, mn = r
        out['quartiers'][quartier] = {
            'commune': commune, 'lat': lat, 'lon': lon, 'km': km, 'minutes': mn,
        }
        print('  %-26s %-16s %5.1f km  %3d min' % (quartier, commune, km, mn))

    dest = sys.argv[1] if len(sys.argv) > 1 else 'distances_quartiers.json'
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('\n%d quartiers -> %s' % (len(out['quartiers']), dest))


if __name__ == '__main__':
    main()
