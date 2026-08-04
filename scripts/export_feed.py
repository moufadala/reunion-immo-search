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
from enrich_source_details_v3 import llm_input_hash  # noqa: E402

DB = os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db')
EVENTS_DB = os.environ.get('IMMO_EVENTS_DB', '/opt/data/artifacts/immo-alerts/history.sqlite')
ROOT = '/opt/data/projects/reunion-immo-search'
OUT = os.environ.get('IMMO_FEED_OUT', ROOT + '/artifacts/app/feed.json')
# distances precalculees quartier -> point de reference.
# Le point de reference lui-meme reste dans ce fichier COTE SERVEUR et n'est
# jamais recopie dans le feed servi : seules les durees en sortent.
DIST = ROOT + '/config/distances_quartiers.json'

COMMUNES = ['Saint-Denis', 'Sainte-Marie', 'Sainte-Suzanne', 'Saint-André']
SERVE_COMMUNES = ['Saint-Denis', 'Sainte-Marie', 'Sainte-Suzanne']

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


EXCLUDED_COMMUNE_NORMS = {norm('Saint-André')}
EXCLUDED_QUARTIER_LABELS = {
    'bellepierre': 'Bellepierre',
    'montgaillard': 'Montgaillard',
    'bas-de-la-riviere': 'Bas de la Rivière',
}
DESCRIPTION_EXCLUDED_QUARTIERS = ('bellepierre', 'montgaillard', 'la-montagne', 'bas-de-la-riviere')
DESCRIPTION_LOCATION_RE = re.compile(
    r'\b(?:situe(?:e|es|s)?\s+a|situe(?:e|es|s)?\s+au|a|au|aux|location(?:\s+(?:de|d|un|une|appartement|studio|maison|t[0-9]|f[0-9]|meuble|meublee)){0,8}|louer\s+a)\s+(?P<q>bellepierre|montgaillard|la\s+montagne|bas\s+de\s+la\s+riviere)\b'
)
DESCRIPTION_REPERE_RE = re.compile(
    r'\b(?:vue|face|proche|pres|minutes?\s+de|a\s+\d+\s*(?:min|minutes?)\s+de|acces|route|lycee\s+de|chu\s+de|secteur|preference|souhait)\b'
)


def excluded_quartier_from_field(quartier):
    n = norm(quartier)
    if not n:
        return None
    for needle, label in EXCLUDED_QUARTIER_LABELS.items():
        if needle in n:
            return label
    # La Montagne doit attraper La Montagne 8eme / 15eme, mais pas "vue sur la montagne".
    if re.search(r'(^|-)la-montagne($|-)', n):
        return 'La Montagne'
    return None


def residential_flag_looks_like_amenity_false_positive(title, reasons):
    """Le flag non-résidentiel historique est trop large sur parking/garage/terrain.

    Il confond parfois une commodité d'un logement avec une annonce de parking,
    garage ou terrain. En cas de doute, on garde le logement visible.
    """
    title_n = norm(title).replace('-', ' ')
    if not re.search(r'\b(t[1-6]|f[1-6]|studio|appartement|maison|villa|duplex)\b', title_n):
        return False
    joined = ' '.join(str(x) for x in (reasons or [])).lower()
    weak = any(k in joined for k in ('strong_keyword=parking', 'strong_keyword=garage', 'strong_keyword=terrain'))
    strong_non_res = any(k in joined for k in (
        'property_type=commercial', 'property_type=box', 'local commercial',
        'local professionnel', 'bureau', 'bureaux', 'locaux commerciaux'))
    return weak and not strong_non_res


def excluded_quartier_from_description(title, description, location_label=None):
    """Exclusion prudente: seulement si le quartier apparait comme lieu.

    Les mentions de repere (vue/proche/CHU/lycee/secteur souhait...) ne cachent
    jamais une annonce. Le titre est inclus parce que certains portails y portent
    la tournure de lieu (ex. "LOCATION - ST-DENIS MONTGAILLARD").
    """
    raw_title = str(title or '')
    hay = ' '.join(str(x or '') for x in (title, description, location_label))
    txt = norm(hay).replace('-', ' ')
    title_norm = norm(raw_title).replace('-', ' ')

    # Cas tres fiable dans les donnees actuelles: Montgaillard dans le titre.
    # Les faux positifs mesures par Moufadal concernent Bellepierre/La Montagne
    # comme reperes ou souhaits dans la description, pas Montgaillard en titre.
    if 'montgaillard' in title_norm:
        return 'Montgaillard'

    for m in re.finditer(r'\bmontgaillard\b', txt):
        before = txt[max(0, m.start() - 100):m.start()]
        if DESCRIPTION_REPERE_RE.search(before):
            continue
        if re.search(r'\b(?:situe(?:e|es|s)?\s+a\s+saint\s+denis|location\b.{0,80}\bsaint\s+denis|loue\b.{0,80}\bsaint\s+denis)\b', before):
            return 'Montgaillard'

    for m in DESCRIPTION_LOCATION_RE.finditer(txt):
        before = txt[max(0, m.start() - 70):m.start()]
        if DESCRIPTION_REPERE_RE.search(before):
            continue
        q = norm(m.group('q'))
        return {
            'bellepierre': 'Bellepierre',
            'montgaillard': 'Montgaillard',
            'la-montagne': 'La Montagne',
            'bas-de-la-riviere': 'Bas de la Rivière',
        }.get(q)
    return None


def coverage_from_feed(now, listings):
    active = [x for x in listings if x.get('active')]
    communes = Counter(x.get('commune') or 'Non renseignée' for x in active)
    regions = Counter('Nord-Est' for _ in active)
    precision = Counter(x.get('location_precision_label') or x.get('location_precision') or 'Inconnue' for x in active)
    rents = [as_int(x.get('rent')) for x in active if as_int(x.get('rent')) is not None]
    surfaces = [as_float(x.get('surface')) for x in active if as_float(x.get('surface')) is not None]
    return {
        'generated_at': now.isoformat(),
        'population': 'feed.json listings actives apres filtres produit',
        'count': len(active),
        'cities': dict(communes.most_common()),
        'regions': dict(regions.most_common()),
        'geo_confidence': {'commune': precision.get('Commune seule', 0), 'by_label': dict(precision.most_common())},
        'price_min': min(rents) if rents else None,
        'price_max': max(rents) if rents else None,
        'surface_min': min(surfaces) if surfaces else None,
        'surface_max': max(surfaces) if surfaces else None,
    }


def load_llm_extractions(c):
    """Return latest grounded LLM extraction per listing.

    The table is optional: fresh deployments and test DBs may not have it yet.
    Values are only used when their input_hash still matches the current source
    text, so stale extraction never overrides a newer description.
    """
    latest = {}
    try:
        rows = c.execute(
            '''
            SELECT source_site, source_id, extracted_at, model, input_hash, fields_json
            FROM listing_llm_extraction
            WHERE COALESCE(grounded, 1)=1
            ORDER BY extracted_at ASC
            '''
        )
    except sqlite3.OperationalError:
        return latest
    for r in rows:
        try:
            fields = json.loads(r['fields_json'] or '{}')
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(fields, dict):
            continue
        latest[(r['source_site'], str(r['source_id']))] = {
            'model': r['model'],
            'extracted_at': r['extracted_at'],
            'input_hash': r['input_hash'],
            'fields': fields,
        }
    return latest


def as_int(v):
    if v in (None, ''):
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def as_float(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_price_changes(since_iso):
    """Dernier price_changed utile par annonce, lu dans l'historique.

    Garde produit côté export: on ne prépare que les baisses. La garde finale
    `new == rent courant` reste appliquée au moment d'émettre le listing, car
    elle dépend du prix affiché par l'annonce active.
    """
    latest = {}
    if not os.path.exists(EVENTS_DB):
        return latest
    h = None
    try:
        h = sqlite3.connect('file:%s?mode=ro' % EVENTS_DB, uri=True)
        h.row_factory = sqlite3.Row
        rows = h.execute(
            '''
            SELECT event_id, listing_id, event_at, old_value, new_value
            FROM listing_events
            WHERE event_type='price_changed' AND event_at >= ?
            ORDER BY event_at ASC, event_id ASC
            ''',
            (since_iso,),
        ).fetchall()
    except sqlite3.Error:
        return latest
    finally:
        try:
            if h:
                h.close()
        except Exception:
            pass
    for r in rows:
        old = as_int(r['old_value'])
        new = as_int(r['new_value'])
        if old is None or new is None or new >= old:
            continue
        # Garde anti-mensonge: certains scrapers basculent de loyer total vers
        # charges/montant partiel et créent de fausses "baisses" de plusieurs
        # milliers d'euros (ex. 3450 -> 450). Une baisse >50% est trop fragile
        # pour être affichée comme opportunité sans validation humaine.
        if old > 0 and ((old - new) / old) > 0.50:
            continue
        latest[str(r['listing_id'])] = {
            'ancien': old,
            'new': new,
            'delta': new - old,
            'depuis': str(r['event_at'])[:10],
            'event_id': r['event_id'],
        }
    return latest


def market_counts_from_history():
    """Signal marche des 7 derniers jours issu directement de listing_events."""
    if not os.path.exists(EVENTS_DB):
        return {'retirees_7j': 0}
    since_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    h = None
    try:
        h = sqlite3.connect('file:%s?mode=ro' % EVENTS_DB, uri=True)
        h.row_factory = sqlite3.Row
        row = h.execute(
            '''
            SELECT COUNT(*) AS n
            FROM listing_events
            WHERE event_type='disappeared' AND event_at >= ?
            ''',
            (since_iso,),
        ).fetchone()
    except sqlite3.Error:
        return {'retirees_7j': 0}
    finally:
        try:
            if h:
                h.close()
        except Exception:
            pass
    return {'retirees_7j': int(row['n'] if row else 0)}


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


def bathroom_state(detail: dict) -> dict:
    """Public, non-filtering bathroom display state.

    Moufadal arbitration 2026-07-30: a bathtub is a preference, never an
    exclusion criterion. We only expose what we know so cards can display one of
    four human states without inventing a filter.
    """
    bathtub = detail.get('bathtub')
    nb_sdb = detail.get('nb_sdb')
    if bathtub == 1:
        return {'state': 'baignoire', 'label': 'baignoire'}
    if bathtub == 0:
        return {'state': 'douche_seulement', 'label': 'douche seulement'}
    if nb_sdb:
        return {'state': 'salle_de_bain_equipement_inconnu', 'label': 'salle de bain, équipement non précisé'}
    return {'state': 'non_precise', 'label': 'non précisé'}


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
    galeries_man = {}
    try:
        man = json.load(open(ROOT + '/artifacts/app/photos_manifest.json', encoding='utf-8'))
        for cle, v in (man.get('photos') or {}).items():
            site, _, sid = cle.partition(':')
            k = (site, sid)
            loc = v.get('local')
            if loc and os.path.exists(os.path.join(ROOT, 'artifacts/app', loc.lstrip('/'))):
                thumbs[k] = loc
            locs = ['/' + str(u).lstrip('/') for u in (v.get('locals') or [])
                    if u and os.path.exists(os.path.join(ROOT, 'artifacts/app', str(u).lstrip('/')))]
            if len(locs) > 1:
                # Nouvelle source canonique de galerie : manifeste local,
                # produit par cache_photos.py depuis listing_detail.photo_urls.
                galeries_man[k] = locs
    except (OSError, ValueError):
        pass
    # Filet : anciennes vignettes referencees par le pipeline precedent et pas
    # encore reprises par le manifeste. On ne perd rien de ce qui existe deja.
    # Trouve le 27/07 (soir), remonte par Moufadal apres coup ("je pouvais pas
    # toutes les regarder") : ce meme fichier legacy contient DEJA une galerie
    # multi-photos par annonce (local_image_urls, alimentee chaque jour par
    # enrich_listing_galleries.py) -- jamais reprise par ce pipeline-ci, qui
    # n'a toujours servi qu'UNE photo. On la recupere ici, sans y toucher.
    galleries = {}
    try:
        old = json.load(open(ROOT + '/artifacts/app/listings.json', encoding='utf-8'))
        items = old if isinstance(old, list) else (old.get('items') or old.get('listings') or [])
        for it in items:
            k2 = (it.get('source'), str(it.get('source_id')))
            u = it.get('local_image_url')
            if k2 not in thumbs and u and os.path.exists(os.path.join(ROOT, 'artifacts/app', u)):
                thumbs[k2] = '/' + u.lstrip('/')
            urls = it.get('local_image_urls')
            if isinstance(urls, list) and len(urls) > 1:
                ok = ['/' + u2.lstrip('/') for u2 in urls
                      if u2 and os.path.exists(os.path.join(ROOT, 'artifacts/app', u2.lstrip('/')))]
                if len(ok) > 1:
                    galleries[k2] = ok
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
    llm_extractions = load_llm_extractions(c)

    now = datetime.now(timezone.utc)
    d7 = (now - timedelta(days=7)).isoformat()
    d14 = (now - timedelta(days=14)).isoformat()
    d30 = (now - timedelta(days=30)).isoformat()
    price_changes = load_price_changes(d14)
    market_history = market_counts_from_history()

    listings = []
    hors_perimetre = 0
    filtres_produit = Counter()
    diagnostics_actifs = Counter()

    def compter_exclusion(regle, listing):
        filtres_produit[regle] += 1
        if listing.get('active'):
            filtres_produit[regle + '_actives'] += 1

    for r in c.execute('select * from rental_listings order by seen_last_at desc'):
        k = (r['source_site'], r['source_id'])
        e = enrich.get(k, {})
        d = detail.get(k, {})

        # Trouve le 27/07 (soir) : les scrapers n'ont pas de filtre commune a
        # la source (ex. zimo/citya ramenent toute l'ile) -- la base peut donc
        # contenir des annonces hors Nord+Est en attendant le prochain passage
        # de scripts/scope/purge_nord_est.py. On ne les affiche jamais dans le
        # feed public (mais on ne les supprime pas de la base ici : lecture
        # seule, rien d'irreversible).
        if gq.looks_out_of_scope(r['city'], r['district'], e.get('city_normalized'), e.get('zone_normalized')):
            hors_perimetre += 1
            continue

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
        source_text = d.get('description_full') or r['description'] or ''
        llm = llm_extractions.get((r['source_site'], str(r['source_id'])))
        llm_fields = None
        llm_status = 'absent'
        if llm:
            if llm.get('input_hash') == llm_input_hash(source_text):
                fields = llm.get('fields') or {}
                llm_fields = {
                    'quartier_precis': fields.get('quartier_precis'),
                    'proximites': fields.get('proximites') if isinstance(fields.get('proximites'), list) else [],
                    'routes_axes': fields.get('routes_axes') if isinstance(fields.get('routes_axes'), list) else [],
                    'points_repere': fields.get('points_repere') if isinstance(fields.get('points_repere'), list) else [],
                    'model': llm.get('model'),
                    'extracted_at': llm.get('extracted_at'),
                    'input_hash': llm.get('input_hash'),
                }
                llm_status = 'fresh'
                if not quartier and fields.get('quartier_precis'):
                    quartier = fields.get('quartier_precis')
            else:
                llm_status = 'stale'

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

        k = (r['source_site'], str(r['source_id']))
        pc = price_changes.get('%s:%s' % (r['source_site'], r['source_id']))
        changement_prix = None
        rent_current = as_int(r['rent_eur'])
        if pc and rent_current is not None and pc['new'] == rent_current and bool(r['is_active']):
            changement_prix = {
                'ancien': pc['ancien'],
                'delta': pc['delta'],
                'depuis': pc['depuis'],
            }
        try:
            residential_reasons = json.loads(e.get('residential_reasons_json') or '[]')
        except (TypeError, ValueError):
            residential_reasons = []
        raw_residential = bool(e.get('is_residential', 1))
        weak_non_residential_flag = (
            not raw_residential
            and residential_flag_looks_like_amenity_false_positive(r['title'], residential_reasons)
        )
        public_residential = raw_residential or weak_non_residential_flag

        listing = {
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
            'changement_prix': changement_prix,
            'charges': d.get('charges_eur') if d.get('charges_eur') is not None else r['charges_eur'],
            'surface': r['surface_m2'],
            'rooms': r['rooms'],
            'bedrooms': d.get('bedrooms') if d.get('bedrooms') is not None else r['bedrooms'],
            'type': e.get('property_type_normalized') or r['property_type'],
            'residential': public_residential,
            'residential_raw': raw_residential,
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
            'bathroom': bathroom_state(d),
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
            'image': thumbs.get(k),
            'image_locale': k in thumbs,
            'images': galeries_man.get(k) or galleries.get(k)
                      or ([thumbs[k]] if k in thumbs else []),
            'description': source_text,
            'detail_read': bool(d.get('http_status') == 200),
            'llm_extraction': llm_fields,
            'llm_extraction_status': llm_status,
        }

        if listing['active']:
            diagnostics_actifs['depart_actives'] += 1
            if norm(listing.get('commune')) == norm('Saint-Denis'):
                diagnostics_actifs['saint_denis_depart'] += 1
                if norm(listing.get('quartier')) == norm('Sainte-Marie'):
                    diagnostics_actifs['saint_denis_quartier_sainte_marie'] += 1
                q_diag = excluded_quartier_from_field(listing.get('quartier'))
                if q_diag:
                    diagnostics_actifs['saint_denis_quartier_champ'] += 1
                    diagnostics_actifs['saint_denis_quartier_champ:' + q_diag] += 1
                elif excluded_quartier_from_description(
                        listing.get('title'), listing.get('description'), listing.get('location_label')):
                    diagnostics_actifs['saint_denis_quartier_description'] += 1
            if norm(listing.get('commune')) in EXCLUDED_COMMUNE_NORMS:
                diagnostics_actifs['commune_saint_andre'] += 1
            if listing['residential_raw'] is False:
                diagnostics_actifs['non_residentiel_flag_brut'] += 1
            if weak_non_residential_flag:
                diagnostics_actifs['non_residentiel_flag_faible_garde'] += 1
            if listing['residential'] is False:
                diagnostics_actifs['non_residentiel_servi_exclu'] += 1
            if as_int(listing.get('rent')) is not None and as_int(listing.get('rent')) > 6000:
                diagnostics_actifs['loyer_sup_6000'] += 1
            if listing.get('surface') is not None and float(listing.get('surface')) < 9:
                diagnostics_actifs['surface_inf_9'] += 1

        # --- filtres produit publics (non destructifs DB, mais annonces non servies)
        # Ordre volontaire: chaque annonce est comptee dans la premiere regle qui
        # l'ecarte, pour que le rapport ne mente pas par double comptage.
        if listing['residential'] is False:
            compter_exclusion('non_residentiel', listing)
            continue
        if as_int(listing.get('rent')) is not None and as_int(listing.get('rent')) > 6000:
            compter_exclusion('loyer_sup_6000', listing)
            continue
        surface_value = as_float(listing.get('surface'))
        if surface_value is not None and surface_value < 9:
            compter_exclusion('surface_inf_9', listing)
            continue
        if norm(listing.get('commune')) in EXCLUDED_COMMUNE_NORMS:
            compter_exclusion('commune_saint_andre', listing)
            continue
        if norm(listing.get('commune')) == norm('Saint-Denis'):
            q_exclu = excluded_quartier_from_field(listing.get('quartier'))
            if q_exclu:
                compter_exclusion('saint_denis_quartier_champ', listing)
                compter_exclusion('saint_denis_quartier_champ:' + q_exclu, listing)
                continue
            q_desc = excluded_quartier_from_description(
                listing.get('title'), listing.get('description'), listing.get('location_label'))
            if q_desc:
                compter_exclusion('saint_denis_quartier_description', listing)
                compter_exclusion('saint_denis_quartier_description:' + q_desc, listing)
                continue

        listings.append(listing)

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
        'perimetre': SERVE_COMMUNES,
        'hors_perimetre_exclues': hors_perimetre,
        'exclusions_produit': dict(filtres_produit),
        'diagnostics_actifs_avant_filtres': dict(diagnostics_actifs),
        'total': len(listings),
        'actives': movements['actives'],
        'avec_point_carte': sum(1 for x in listings if x['lat']),
        'avec_trajet': sum(1 for x in listings if x['trajet']),
        'avec_photo_locale': sum(1 for x in listings if x['image']),
        'avec_llm_extraction': sum(1 for x in listings if x.get('llm_extraction_status') == 'fresh'),
        'llm_extraction_stale': sum(1 for x in listings if x.get('llm_extraction_status') == 'stale'),
        'actives_sans_photo': sum(1 for x in listings if x['active'] and not x['image']),
        'detail_lu': sum(1 for x in listings if x['detail_read']),
        'fraiches': sum(1 for x in listings if x['fraiche'] and x['active']),
        'marche': {
            'retirees_7j': market_history.get('retirees_7j', 0),
            'nouvelles_7j': movements['nouvelles_7j'],
            'actives': movements['actives'],
        },
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
    coverage_out = os.path.join(os.path.dirname(OUT), 'coverage.json')
    coverage = coverage_from_feed(now, listings)
    if coverage['count'] != movements['actives']:
        raise RuntimeError('coverage count mismatch: %s != %s' % (coverage['count'], movements['actives']))

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'listings': listings, 'movements': movements,
                   'sources': sources}, f, ensure_ascii=False, separators=(',', ':'))
    with open(coverage_out, 'w', encoding='utf-8') as f:
        json.dump(coverage, f, ensure_ascii=False, separators=(',', ':'))

    print('feed ecrit : %s (%.2f Mo)' % (OUT, os.path.getsize(OUT) / 1e6))
    print('coverage ecrit : %s (count=%d)' % (coverage_out, coverage['count']))
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print('mouvements :', json.dumps(movements, ensure_ascii=False))


if __name__ == '__main__':
    main()
