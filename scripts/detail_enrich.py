#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lecture de la page de detail de chaque annonce -> localisation la plus precise
possible + criteres absents de la vignette (etage, ascenseur, baignoire, charges).

Ordre d'extraction (du plus fiable au moins fiable) :
  1. JSON-LD schema.org  -> streetAddress / postalCode / geo.latitude
  2. meta og: / place:   -> street-address / latitude
  3. carte embarquee     -> lat/lon dans une iframe Google/Leaflet/OSM
  4. texte complet       -> rue, residence, etage, ascenseur, baignoire

On n'INVENTE jamais : chaque annonce recoit un `precision` explicite
(adresse_exacte > rue > residence > quartier > commune > inconnu) et la
`source` de l'information. Une extraction ratee laisse le champ vide.

Usage:
  detail_enrich.py --limit 30            # essai
  detail_enrich.py --all --delay 4       # passe complete, lente
  detail_enrich.py --stats               # etat de la couverture
"""
from __future__ import annotations

import argparse
import gzip
import html
import io
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.description_observability import (
    assess_description,
    select_description,
)

DESCRIPTION_EXTRACTOR_VERSION = "detail-enrich-v2"
DB = os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db')
CACHE = '/opt/data/projects/reunion-immo-search/artifacts/detail_pages'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

DDL = """
CREATE TABLE IF NOT EXISTS listing_detail (
  source_site TEXT NOT NULL,
  source_id   TEXT NOT NULL,
  fetched_at  TEXT NOT NULL,
  http_status INTEGER,
  -- localisation
  address     TEXT,      -- adresse la plus precise trouvee
  street      TEXT,      -- nom de rue seul
  residence   TEXT,      -- nom de residence / lotissement
  postal_code TEXT,
  locality    TEXT,
  lat         REAL,
  lon         REAL,
  precision   TEXT,      -- adresse_exacte|rue|residence|quartier|commune|inconnu
  geo_source  TEXT,      -- jsonld|meta|carte|texte|aucun
  -- criteres absents de la vignette
  floor       TEXT,
  has_elevator INTEGER,  -- 1 oui / 0 non explicite / NULL inconnu
  bathtub     INTEGER,   -- 1 baignoire / 0 douche seule / NULL inconnu
  furnished   INTEGER,
  charges_eur INTEGER,
  bedrooms    INTEGER,
  -- texte
  description_full TEXT,
  description_content_state TEXT,
  description_length INTEGER,
  description_sha256 TEXT,
  description_full_text_evidence INTEGER,
  description_succeeded_at TEXT,
  description_attempt_status TEXT,
  description_attempted_at TEXT,
  description_attempt_length INTEGER,
  description_attempt_sha256 TEXT,
  description_attempt_error TEXT,
  description_extractor_version TEXT,
  notes       TEXT,
  PRIMARY KEY (source_site, source_id)
);
"""

# colonnes ajoutees apres coup (2026-07-27, demande confort interieur)
COLS_CONFORT = [
    ('nb_sdb', 'INTEGER'), ('nb_wc', 'INTEGER'), ('wc_separe', 'INTEGER'),
    ('jardin', 'INTEGER'), ('veranda', 'INTEGER'), ('terrasse', 'INTEGER'),
    ('parking', 'INTEGER'), ('piscine', 'INTEGER'), ('clim', 'INTEGER'),
    ('niveaux', 'INTEGER'),
]
COLS_DESCRIPTION_OBSERVABILITY = [
    ('description_content_state', 'TEXT'),
    ('description_length', 'INTEGER'),
    ('description_sha256', 'TEXT'),
    ('description_full_text_evidence', 'INTEGER'),
    ('description_succeeded_at', 'TEXT'),
    ('description_attempt_status', 'TEXT'),
    ('description_attempted_at', 'TEXT'),
    ('description_attempt_length', 'INTEGER'),
    ('description_attempt_sha256', 'TEXT'),
    ('description_attempt_error', 'TEXT'),
    ('description_extractor_version', 'TEXT'),
]




def migrer(c):
    existantes = {r[1] for r in c.execute('pragma table_info(listing_detail)')}
    for nom, typ in COLS_CONFORT + COLS_DESCRIPTION_OBSERVABILITY:
        if nom not in existantes:
            c.execute('alter table listing_detail add column %s %s' % (nom, typ))
    c.commit()

# ---------------------------------------------------------------- helpers


def now():
    return datetime.now(timezone.utc).isoformat()


def description_fields(existing, candidate, *, attempted_at, http_status,
                       error=None, existing_succeeded_at=None,
                       full_text_evidence=False,
                       existing_full_text_evidence=False):
    """Keep content and attempt evidence separate.

    HTTP 200 is transport evidence only. The selected user-facing text is allowed
    to change solely through select_description; empty/boilerplate attempts remain
    visible in the attempt columns without erasing the previous source text.
    """
    attempted_dt = datetime.fromisoformat(str(attempted_at).replace('Z', '+00:00'))
    observation = assess_description(
        candidate,
        extractor_version=DESCRIPTION_EXTRACTOR_VERSION,
        attempted_at=attempted_dt,
        http_status=http_status,
        error=error,
        now=attempted_dt,
        full_text_evidence=full_text_evidence,
    )
    selection = select_description(
        existing,
        candidate,
        candidate_observation=observation,
        existing_succeeded_at=existing_succeeded_at,
        now=attempted_dt,
        existing_full_text_evidence=existing_full_text_evidence,
    )
    succeeded_at = existing_succeeded_at
    if not selection.kept_existing and observation.succeeded_at is not None:
        succeeded_at = observation.succeeded_at.isoformat()
    return {
        'description_full': selection.text,
        'description_content_state': selection.content_state,
        'description_length': selection.length,
        'description_sha256': selection.sha256,
        'description_full_text_evidence': int(selection.full_text_evidence),
        'description_succeeded_at': succeeded_at,
        'description_attempt_status': observation.status,
        'description_attempted_at': attempted_at,
        'description_attempt_length': observation.length,
        'description_attempt_sha256': observation.sha256,
        'description_attempt_error': error,
        'description_extractor_version': DESCRIPTION_EXTRACTOR_VERSION,
        'description_selection_reason': selection.reason,
    }


def clean(s):
    if not s:
        return ''
    s = html.unescape(str(s))
    s = s.replace(' ', ' ')
    s = re.sub(r'<\s*br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'<script.*?</script>', ' ', s, flags=re.S | re.I)
    s = re.sub(r'<style.*?</style>', ' ', s, flags=re.S | re.I)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\n\s*\n+', '\n\n', s)
    return s.strip()


def fetch(url, timeout=25):
    # Trouve le 27/07 (soir) : sans Referer, zimo (et probablement d'autres)
    # renvoie 403 sur SA PROPRE page de detail -- confirme en testant avec/sans
    # le header sur la meme URL. enrich_source_details_v3.py le savait deja
    # (son fetch texte l'envoie) mais ce fetch-ci, utilise pour les champs
    # structures (adresse/etage/lat-lon), ne l'envoyait pas.
    host = urllib.parse.urlparse(url).netloc
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip',
        'Connection': 'close',
        'Referer': 'https://%s/' % host,
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        enc = 'utf-8'
        ct = r.headers.get('Content-Type', '')
        m = re.search(r'charset=([\w-]+)', ct, re.I)
        if m:
            enc = m.group(1)
        return r.status, raw.decode(enc, 'replace')


# Trouve le 27/07 (soir) : seloger renvoie 403 sur fetch() meme avec Referer
# (contrairement a zimo). Confirme deux fois independamment (probe du matin +
# reverifie ce soir) : la page de detail charge normalement (200) via le
# navigateur headless chromium-cdp deja utilise en prod pour les LISTES
# seloger (scripts/seloger_multi_page.py). Meme pattern de connexion,
# reutilise tel quel. Un seul navigateur/contexte garde ouvert pour tout le
# run (pas de reconnexion par page) ; ferme explicitement par close_cdp().
_CDP_BROWSER = None
_CDP_CONTEXT = None


def _cdp_context():
    global _CDP_BROWSER, _CDP_CONTEXT
    if _CDP_CONTEXT is not None:
        return _CDP_CONTEXT
    import socket
    from playwright.sync_api import sync_playwright
    host = os.environ.get('IMMO_CDP_HOST', 'chromium-cdp')
    port = os.environ.get('IMMO_CDP_PORT', '9223')
    try:
        host = socket.gethostbyname(host)
    except OSError:
        pass
    _playwright = sync_playwright().start()
    _CDP_BROWSER = _playwright.chromium.connect_over_cdp('http://%s:%s' % (host, port))
    _CDP_CONTEXT = _CDP_BROWSER.new_context(
        locale='fr-FR', timezone_id='Indian/Reunion', viewport={'width': 1366, 'height': 900})
    return _CDP_CONTEXT


def close_cdp():
    global _CDP_BROWSER, _CDP_CONTEXT
    if _CDP_BROWSER is not None:
        try:
            _CDP_BROWSER.close()
        except Exception:
            pass
    _CDP_BROWSER = None
    _CDP_CONTEXT = None


def fetch_cdp(url, timeout=45000):
    ctx = _cdp_context()
    page = ctx.new_page()
    try:
        rep = page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        page.wait_for_timeout(2500)
        status = rep.status if rep else 200
        return status, page.content()
    finally:
        page.close()


# ---------------------------------------------------------------- extraction

# Types schema.org qui decrivent LE BIEN. Tout le reste (Organization,
# RealEstateAgent, LocalBusiness, WebSite...) porte l'adresse de l'AGENCE.
BIEN_TYPES = {'realestatelisting', 'residence', 'apartment', 'house', 'singlefamilyresidence',
              'accommodation', 'product', 'offer', 'place', 'suite'}
AGENCE_TYPES = {'organization', 'realestateagent', 'localbusiness', 'website',
                'webpage', 'corporation', 'breadcrumblist', 'person'}


def walk_jsonld(node, out, ctx=None):
    """Ramasse address / geo, en retenant DE QUEL TYPE ils viennent."""
    if isinstance(node, dict):
        t = node.get('@type') or node.get('type') or ''
        if isinstance(t, list):
            t = t[0] if t else ''
        t = str(t).lower()
        here = t if t else ctx
        bucket = 'bien' if t in BIEN_TYPES else ('agence' if t in AGENCE_TYPES else (ctx or 'inconnu'))

        addr = node.get('address')
        if isinstance(addr, dict):
            d = out.setdefault(bucket, {})
            d.setdefault('street', addr.get('streetAddress'))
            d.setdefault('postal_code', addr.get('postalCode'))
            d.setdefault('locality', addr.get('addressLocality'))
        elif isinstance(addr, str) and len(addr) > 5:
            out.setdefault(bucket, {}).setdefault('address_raw', addr)
        geo = node.get('geo')
        if isinstance(geo, dict):
            try:
                d = out.setdefault(bucket, {})
                d.setdefault('lat', float(geo.get('latitude')))
                d.setdefault('lon', float(geo.get('longitude')))
            except (TypeError, ValueError):
                pass
        for k in ('numberOfBedrooms', 'numberOfRooms'):
            v = node.get(k)
            if isinstance(v, (int, float)):
                out.setdefault('bien', {}).setdefault('bedrooms', int(v))
        for v in node.values():
            walk_jsonld(v, out, here if here in ('bien', 'agence') else bucket)
    elif isinstance(node, list):
        for v in node:
            walk_jsonld(v, out, ctx)


def from_jsonld(page):
    """Retourne uniquement ce qui decrit LE BIEN. On jette l'adresse de l'agence."""
    buckets = {}
    for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            page, re.S | re.I):
        try:
            walk_jsonld(json.loads(m.group(1).strip()), buckets)
        except Exception:
            continue
    return buckets.get('bien', {})


def from_meta(page):
    out = {}
    pairs = re.findall(
        r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
        page, re.I)
    pairs += re.findall(
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']([^"\']+)["\']',
        page, re.I)
    d = {}
    for a, b in pairs:
        if ':' in a or a.startswith('og') or a.startswith('place'):
            d[a.lower()] = b
        else:
            d[b.lower()] = a
    for k in ('og:street-address', 'street-address', 'address'):
        if d.get(k):
            out['street'] = d[k]
            break
    for k in ('place:location:latitude', 'og:latitude', 'geo.position'):
        v = d.get(k)
        if not v:
            continue
        if ';' in v:
            try:
                out['lat'], out['lon'] = [float(x) for x in v.split(';')[:2]]
            except ValueError:
                pass
        else:
            try:
                out['lat'] = float(v)
            except ValueError:
                pass
        break
    for k in ('place:location:longitude', 'og:longitude'):
        if d.get(k) and 'lon' not in out:
            try:
                out['lon'] = float(d[k])
            except ValueError:
                pass
    if d.get('og:locality'):
        out['locality'] = d['og:locality']
    if d.get('og:postal-code'):
        out['postal_code'] = d['og:postal-code']
    return out


# La Reunion : lat entre -21.4 et -20.8 / lon entre 55.2 et 55.9
def plausible(lat, lon):
    try:
        return -21.45 < float(lat) < -20.80 and 55.15 < float(lon) < 55.95
    except (TypeError, ValueError):
        return False


MAP_PATTERNS = [
    r'[?&]q=(-?\d+\.\d+)[,%]+(?:2C)?(-?\d+\.\d+)',
    r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)',
    r'[?&]mlat=(-?\d+\.\d+)&mlon=(-?\d+\.\d+)',
    r'[?&]center=(-?\d+\.\d+)[,%]+(?:2C)?(-?\d+\.\d+)',
    r'data-lat(?:itude)?=["\'](-?\d+\.\d+)["\'][^>]*data-l(?:ng|on|ongitude)=["\'](-?\d+\.\d+)["\']',
    r'\blat(?:itude)?["\']?\s*[:=]\s*["\']?(-?\d+\.\d+)["\']?\s*,\s*["\']?l(?:ng|on|ongitude)["\']?\s*[:=]\s*["\']?(-?\d+\.\d+)',
    r'LatLng\(\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*\)',
]


def from_map(page):
    for pat in MAP_PATTERNS:
        for m in re.finditer(pat, page, re.I):
            lat, lon = m.group(1), m.group(2)
            if plausible(lat, lon):
                return {'lat': float(lat), 'lon': float(lon)}
    return {}


RUE = (r'(?:rue|avenue|av\.|chemin|impasse|all[ée]e|boulevard|bd|route|ruelle|'
       r'sentier|place|cours|quai|lotissement)')
RE_ADDR = re.compile(r'\b(\d{1,3}(?:\s?(?:bis|ter))?)\s+(' + RUE + r'\s+[^,.;\n]{3,60})', re.I)
RE_RUE = re.compile(r'\b(' + RUE + r'\s+(?:de\s+|du\s+|des\s+|la\s+|le\s+|l\')?'
                    r'[A-ZÉÈÊÀÂÎÔÛ][^,.;\n]{2,50})')
RE_RESID = re.compile(r'\b(?:r[ée]sidence|domaine|villa|lotissement|parc)\s+'
                      r'((?:[A-ZÉÈÊ][\w\'-]*\s?){1,4})')
RE_ETAGE = re.compile(r'\b(rez[- ]de[- ]chauss[ée]e|rdc|(\d{1,2})\s*(?:er|ème|eme|e)\s*[ée]tage'
                      r'|[ée]tage\s*:?\s*(\d{1,2}))', re.I)
RE_ASC_OUI = re.compile(r'\bavec\s+ascenseur|\bascenseur\s*:?\s*(?:oui|1)\b|\bascenseur\b(?!\s*:\s*non)', re.I)
RE_ASC_NON = re.compile(r'\bsans\s+ascenseur|\bascenseur\s*:?\s*non\b|\bpas\s+d.ascenseur', re.I)
RE_BAIGN = re.compile(r'\bbaignoire\b', re.I)
RE_DOUCHE = re.compile(r'\b(douche|salle\s+d.eau)\b', re.I)
RE_CHARGES = re.compile(r'charges?\s*(?:comprises?|mensuelles?|locatives?)?\s*:?\s*'
                        r'(\d{1,4})\s*(?:€|eur)', re.I)
RE_MEUBLE = re.compile(r'\b(non\s+meubl|meubl)', re.I)
RE_CHAMBRE = re.compile(r'(\d{1,2})\s*chambres?\b', re.I)

# --- demande Moufadal 2026-07-27 : le confort interieur du bien ---
MOTS_NB = {'une': 1, 'un': 1, 'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5}
RE_SDB_N = re.compile(r'\b(\d{1,2}|une|deux|trois|quatre|cinq)\s*salles?\s*de\s*bains?\b', re.I)
RE_SDB_1 = re.compile(r'\bsalle\s*de\s*bains?\b|\bsalle\s*d.eau\b', re.I)
RE_WC_SEP = re.compile(r'\bw\.?\s?c\.?\s*(?:s[ée]par|ind[ée]pendant)|toilettes?\s*(?:s[ée]par|ind[ée]pendant)', re.I)
RE_WC_N = re.compile(r'\b(\d{1,2}|une|deux|trois)\s*(?:w\.?\s?c\.?|toilettes?)\b', re.I)
RE_JARDIN = re.compile(r'\bjardin(?:et)?\b|\bcour\s+(?:privative|arbor)', re.I)
RE_VERANDA = re.compile(r'\bv[ée]randa\b', re.I)
RE_TERRASSE = re.compile(r'\bterrasses?\b|\bbalcons?\b', re.I)
RE_PARKING = re.compile(r'\bparking\b|\bgarage\b|\bplace\s+de\s+stationnement\b|\bcarport\b', re.I)
RE_PISCINE = re.compile(r'\bpiscine\b', re.I)
RE_CLIM = re.compile(r'\bclimatis|\bclim\b', re.I)
# niveaux DANS le bien (duplex, maison a etage) - a ne pas confondre avec
# l'etage de l'appartement dans l'immeuble
RE_NIVEAUX = re.compile(r'\b(duplex|triplex|sur\s+(?:deux|trois|\d)\s+niveaux?|'
                        r'(?:deux|trois|\d)\s+niveaux?|[ée]tage\s+sup[ée]rieur\s+comprenant|'
                        r'maison\s+[àa]\s+[ée]tage)', re.I)


def nb(tok):
    if not tok:
        return None
    t = str(tok).strip().lower()
    return MOTS_NB.get(t, int(t) if t.isdigit() else None)


def from_text_confort(txt):
    """Ce qui rend une annonce vivable, et qu'on ne voyait jamais."""
    o = {}
    m = RE_SDB_N.search(txt)
    if m:
        o['nb_sdb'] = nb(m.group(1))
    elif RE_SDB_1.search(txt):
        o['nb_sdb'] = 1
    if RE_WC_SEP.search(txt):
        o['wc_separe'] = 1
    m = RE_WC_N.search(txt)
    if m:
        o['nb_wc'] = nb(m.group(1))
    o['jardin'] = 1 if RE_JARDIN.search(txt) else None
    o['veranda'] = 1 if RE_VERANDA.search(txt) else None
    o['terrasse'] = 1 if RE_TERRASSE.search(txt) else None
    o['parking'] = 1 if RE_PARKING.search(txt) else None
    o['piscine'] = 1 if RE_PISCINE.search(txt) else None
    o['clim'] = 1 if RE_CLIM.search(txt) else None
    m = RE_NIVEAUX.search(txt)
    if m:
        g = m.group(1).lower()
        if 'duplex' in g:
            o['niveaux'] = 2
        elif 'triplex' in g:
            o['niveaux'] = 3
        elif 'trois' in g:
            o['niveaux'] = 3
        elif 'deux' in g:
            o['niveaux'] = 2
        else:
            mm = re.search(r'(\d)', g)
            o['niveaux'] = int(mm.group(1)) if mm else 2
    return {k: v for k, v in o.items() if v is not None}


def from_text(txt):
    out = {}
    m = RE_ADDR.search(txt)
    if m:
        out['address'] = clean('%s %s' % (m.group(1), m.group(2)))
        out['street'] = clean(m.group(2))
    else:
        m = RE_RUE.search(txt)
        if m:
            out['street'] = clean(m.group(1))
    m = RE_RESID.search(txt)
    if m:
        out['residence'] = clean(m.group(1))
    m = RE_ETAGE.search(txt)
    if m:
        g = m.group(0).lower()
        if 'rez' in g or g.strip() == 'rdc':
            out['floor'] = 'RDC'
        else:
            n = m.group(2) or m.group(3)
            out['floor'] = ('%se etage' % n) if n else None
    if RE_ASC_NON.search(txt):
        out['has_elevator'] = 0
    elif RE_ASC_OUI.search(txt):
        out['has_elevator'] = 1
    if RE_BAIGN.search(txt):
        out['bathtub'] = 1
    elif RE_DOUCHE.search(txt):
        out['bathtub'] = 0
    m = RE_CHARGES.search(txt)
    if m:
        try:
            out['charges_eur'] = int(m.group(1))
        except ValueError:
            pass
    m = RE_MEUBLE.search(txt)
    if m:
        out['furnished'] = 0 if m.group(1).lower().startswith('non') else 1
    m = RE_CHAMBRE.search(txt)
    if m:
        try:
            out['bedrooms'] = int(m.group(1))
        except ValueError:
            pass
    return out


DESCRIPTION_BODY_PATTERNS = (
    r'<div[^>]+class=["\'][^"\']*(?:description|descriptif|texte-annonce)'
    r'[^"\']*["\'][^>]*>(.*?)</div>',
    r'<section[^>]+class=["\'][^"\']*description[^"\']*["\'][^>]*>(.*?)</section>',
)


def structural_description_candidates(page):
    candidates = []
    for pattern in DESCRIPTION_BODY_PATTERNS:
        for match in re.finditer(pattern, page, re.S | re.I):
            text = clean(match.group(1))
            if len(text) >= 10:
                candidates.append(text)
    return candidates


def best_description(page, fallback=''):
    structural = structural_description_candidates(page)
    if structural:
        return max(structural, key=len)
    candidates = []
    for m in re.finditer(r'<meta[^>]+(?:property|name)=["\'](?:og:)?description["\']'
                         r'[^>]+content=["\']([^"\']{40,})["\']', page, re.I):
        candidates.append(clean(m.group(1)))
    candidates = [candidate for candidate in candidates if len(candidate) >= 10]
    if not candidates:
        return fallback
    source_text = max(candidates, key=len)
    return source_text if len(source_text) >= len(fallback or '') else fallback


def decide_precision(rec):
    if rec.get('address'):
        return 'adresse_exacte'
    if rec.get('street'):
        return 'rue'
    if rec.get('residence'):
        return 'residence'
    if rec.get('lat') and rec.get('lon'):
        return 'point_carte'
    return 'inconnu'


# ---------------------------------------------------------------- main

def process(page, fallback_desc):
    rec = {}
    src = []
    j = from_jsonld(page)
    if j:
        for k in ('street', 'postal_code', 'locality', 'bedrooms'):
            if j.get(k):
                rec[k] = clean(j[k]) if isinstance(j[k], str) else j[k]
        if plausible(j.get('lat'), j.get('lon')):
            rec['lat'], rec['lon'] = j['lat'], j['lon']
            src.append('jsonld')
        if j.get('address_raw'):
            rec['address'] = clean(j['address_raw'])
            src.append('jsonld')
        elif rec.get('street'):
            src.append('jsonld')
    if 'lat' not in rec:
        mt = from_meta(page)
        if plausible(mt.get('lat'), mt.get('lon')):
            rec['lat'], rec['lon'] = mt['lat'], mt['lon']
            src.append('meta')
        for k in ('street', 'locality', 'postal_code'):
            if mt.get(k) and not rec.get(k):
                rec[k] = clean(mt[k])
                src.append('meta')
    if 'lat' not in rec:
        mp = from_map(page)
        if mp:
            rec.update(mp)
            src.append('carte')

    detail_candidate = best_description(page, '')
    desc = detail_candidate if len(detail_candidate) >= len(fallback_desc or '') else fallback_desc
    rec['description_full'] = desc
    rec['_description_attempt_candidate'] = detail_candidate
    rec['_description_full_text_evidence'] = bool(
        structural_description_candidates(page))

    # PIEGE PROUVE (2026-07-27) : chercher une adresse dans TOUTE la page ramene
    # l'adresse de l'AGENCE ou des mentions legales (immo974 -> "3 Place de Fontenoy",
    # l'adresse de la CNIL ; ofim -> son propre siege). L'adresse et la residence ne
    # sont donc cherchees QUE dans la description du bien.
    t_addr = from_text(desc)
    for k in ('address', 'street', 'residence'):
        if t_addr.get(k) and not rec.get(k):
            rec[k] = t_addr[k]
            src.append('texte')
    # les equipements peuvent vivre hors description (tableau de caracteristiques)
    t_feat = from_text(desc + '\n' + clean(page)[:20000])
    for k in ('floor', 'has_elevator', 'bathtub', 'furnished', 'charges_eur', 'bedrooms'):
        if t_feat.get(k) is not None and rec.get(k) is None:
            rec[k] = t_feat[k]
    rec.update(from_text_confort(desc + '\n' + clean(page)[:20000]))
    rec['precision'] = decide_precision(rec)
    rec['geo_source'] = '+'.join(dict.fromkeys(src)) or 'aucun'
    return rec


def chemin_cache(ss, si):
    return os.path.join(CACHE, '%s_%s.html' % (ss, re.sub(r'\W+', '_', si)[:60]))


def reparse(c):
    """Re-extrait TOUT depuis les pages deja telechargees. Zero requete reseau :
    on peut donc ajouter des criteres sans redemander quoi que ce soit aux sites."""
    rows = c.execute(
        'select r.source_site, r.source_id, r.description, d.description_full, '
        'd.description_succeeded_at, d.description_full_text_evidence, '
        'd.fetched_at, d.http_status '
        'from rental_listings r '
        'join listing_detail d on d.source_site=r.source_site and d.source_id=r.source_id '
        'where d.http_status=200').fetchall()
    ok = manquant = 0
    champs = ['address', 'street', 'residence', 'postal_code', 'locality', 'lat', 'lon',
              'precision', 'geo_source', 'floor', 'has_elevator', 'bathtub', 'furnished',
              'charges_eur', 'bedrooms', 'description_full'] + [n for n, _ in COLS_CONFORT] + \
             [n for n, _ in COLS_DESCRIPTION_OBSERVABILITY]
    for (ss, si, fb, existing_desc, existing_succeeded_at,
         existing_full_text_evidence, fetched_at, http_status) in rows:
        p = chemin_cache(ss, si)
        if not os.path.exists(p):
            manquant += 1
            continue
        with open(p, encoding='utf-8', errors='replace') as f:
            rec = process(f.read(), fb or '')
        fields = description_fields(
            existing_desc or fb or '',
            rec.pop('_description_attempt_candidate', ''),
            attempted_at=fetched_at or now(),
            http_status=http_status,
            existing_succeeded_at=existing_succeeded_at,
            existing_full_text_evidence=bool(existing_full_text_evidence),
            full_text_evidence=rec.pop('_description_full_text_evidence', False),
        )
        rec.update({name: fields.get(name) for name, _ in COLS_DESCRIPTION_OBSERVABILITY})
        sets = ', '.join('%s=?' % k for k in champs)
        c.execute('update listing_detail set %s where source_site=? and source_id=?' % sets,
                  [rec.get(k) for k in champs] + [ss, si])
        ok += 1
    c.commit()
    print('re-extrait depuis le cache : %d pages (%d absentes du cache)' % (ok, manquant))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=30)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--delay', type=float, default=4.0)
    ap.add_argument('--only-active', action='store_true')
    ap.add_argument('--source')
    ap.add_argument('--exclude', default='',
                    help='portails a sauter, separes par des virgules (ceux qui bloquent)')
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--redo', action='store_true')
    ap.add_argument('--reparse', action='store_true',
                    help='re-extrait depuis les pages en cache, sans reseau')
    ap.add_argument('--filter-agencies', action='store_true',
                    help='neutralise les adresses qui se repetent (= adresse d agence)')
    a = ap.parse_args()

    os.makedirs(CACHE, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute('pragma journal_mode=WAL')
    c.executescript(DDL)
    c.commit()
    migrer(c)

    if a.reparse:
        reparse(c)
        return

    if a.filter_agencies:
        # Une meme adresse qui revient sur plusieurs annonces d'un meme portail
        # n'est pas l'adresse d'un bien : c'est le siege de l'agence (ou les
        # mentions legales). On la neutralise, on ne la devine pas.
        SEUIL = 2
        killed = 0
        for col in ('address', 'street'):
            dups = c.execute(
                'select source_site, [%s], count(*) c from listing_detail '
                'where [%s] is not null and trim([%s])!="" '
                'group by 1,2 having c > ?' % (col, col, col), (SEUIL,)).fetchall()
            for ss, val, k in dups:
                print('  AGENCE detectee  %-12s %-42s (%d annonces) -> neutralise'
                      % (ss, (val or '')[:42], k))
                c.execute('update listing_detail set [%s]=NULL, '
                          'notes=coalesce(notes,"")||" [%s=agence]" '
                          'where source_site=? and [%s]=?' % (col, col, col), (ss, val))
                killed += k
        c.commit()
        # recalcul de la precision apres neutralisation
        for ss, si, ad, st, rs, lat in c.execute(
                'select source_site, source_id, address, street, residence, lat '
                'from listing_detail').fetchall():
            p = ('adresse_exacte' if ad else 'rue' if st else 'residence' if rs
                 else 'point_carte' if lat else 'inconnu')
            c.execute('update listing_detail set precision=? where source_site=? and source_id=?',
                      (p, ss, si))
        c.commit()
        print('\n%d valeurs neutralisees. Precisions recalculees.' % killed)
        return

    if a.stats:
        tot = c.execute('select count(*) from rental_listings').fetchone()[0]
        done = c.execute(
            "select count(*) from listing_detail where http_status=200 "
            "and description_full_text_evidence=1 "
            "and description_attempt_status in ('fetched_complete','source_short_complete')"
        ).fetchone()[0]
        print('annonces: %d | detail texte lu: %d (%.0f%%)' % (tot, done, 100.0 * done / max(tot, 1)))
        print('\n-- precision --')
        for p, k in c.execute('select precision, count(*) from listing_detail '
                              'group by 1 order by 2 desc'):
            print('  %-16s %4d' % (p, k))
        print('\n-- champs recuperes --')
        for col in ('address', 'street', 'residence', 'lat', 'floor', 'has_elevator',
                    'bathtub', 'charges_eur', 'bedrooms'):
            n = c.execute('select count(*) from listing_detail where [%s] is not null' % col).fetchone()[0]
            print('  %-14s %4d / %d  (%.0f%%)' % (col, n, max(done, 1), 100.0 * n / max(done, 1)))
        return

    q = ('select r.source_site, r.source_id, r.url, r.description from rental_listings r '
         'where r.url is not null')
    if not a.redo:
        q += (' and not exists (select 1 from listing_detail d where '
              'd.source_site=r.source_site and d.source_id=r.source_id '
              "and d.http_status=200 and d.description_full_text_evidence=1 "
              "and d.description_attempt_status in ('fetched_complete','source_short_complete'))")
    if a.only_active:
        q += ' and r.is_active=1'
    if a.source:
        q += " and r.source_site='%s'" % a.source.replace("'", '')
    if a.exclude:
        skip = [s.strip() for s in a.exclude.split(',') if s.strip()]
        q += ' and r.source_site not in (%s)' % ','.join("'%s'" % s.replace("'", '') for s in skip)
    q += ' order by r.is_active desc, r.seen_last_at desc'
    rows = c.execute(q).fetchall()

    # ROUND-ROBIN entre portails. Deux raisons :
    #  1) on obtient vite une couverture large plutot que 168 pages d'un seul site ;
    #  2) marteler un meme hote en rafale est le meilleur moyen de se faire bannir
    #     l'IP -- ce qui casserait aussi le scraping de LISTES, qui lui fonctionne.
    par_site = {}
    for r in rows:
        par_site.setdefault(r[0], []).append(r)
    melange = []
    while any(par_site.values()):
        for s in list(par_site):
            if par_site[s]:
                melange.append(par_site[s].pop(0))
            else:
                del par_site[s]
    rows = melange
    if not a.all:
        rows = rows[:a.limit]
    print('a traiter : %d annonces sur %d portails (delay %.1fs/hote)'
          % (len(rows), len({r[0] for r in rows}), a.delay))

    ok = err = 0
    last_host = {}
    for i, (ss, si, url, fb) in enumerate(rows, 1):
        host = re.sub(r'^https?://([^/]+).*', r'\1', url or '')
        gap = time.time() - last_host.get(host, 0)
        if gap < a.delay:
            time.sleep(a.delay - gap + random.uniform(0, 1.2))
        status = None
        rec = {}
        note = ''
        attempted_at = now()
        existing_row = c.execute(
            'select description_full, description_succeeded_at, '
            'description_full_text_evidence from listing_detail '
            'where source_site=? and source_id=?', (ss, si)
        ).fetchone()
        existing_desc = existing_row[0] if existing_row else (fb or '')
        existing_succeeded_at = existing_row[1] if existing_row else None
        existing_full_text_evidence = bool(existing_row[2]) if existing_row else False
        attempt_error = None
        detail_candidate = ''
        full_text_evidence = False
        try:
            if ss == 'seloger':
                status, page = fetch_cdp(url)
            else:
                status, page = fetch(url)
            with open(os.path.join(CACHE, '%s_%s.html' % (ss, re.sub(r'\W+', '_', si)[:60])),
                      'w', encoding='utf-8') as f:
                f.write(page)
            rec = process(page, fb or '')
            detail_candidate = rec.pop('_description_attempt_candidate', '')
            full_text_evidence = rec.pop('_description_full_text_evidence', False)
        except urllib.error.HTTPError as e:
            status = e.code
            note = 'HTTP %s' % e.code
            attempt_error = note
            err += 1
        except Exception as e:
            note = '%s: %s' % (type(e).__name__, str(e)[:120])
            err += 1
            attempt_error = note
        fields = description_fields(
            existing_desc,
            detail_candidate,
            attempted_at=attempted_at,
            http_status=status,
            error=attempt_error,
            existing_succeeded_at=existing_succeeded_at,
            existing_full_text_evidence=existing_full_text_evidence,
            full_text_evidence=full_text_evidence,
        )
        rec.update({name: fields.get(name) for name, _ in COLS_DESCRIPTION_OBSERVABILITY})
        rec['description_full'] = fields['description_full']
        if fields['description_attempt_status'] in {
                'fetched_complete', 'source_short_complete'}:
            ok += 1
        elif not note:
            note = 'HTTP %s sans texte detail exploitable' % status
        last_host[host] = time.time()

        c.execute(
            'insert into listing_detail (source_site, source_id, fetched_at, '
            'http_status, address, street, residence, postal_code, locality, lat, lon, '
            'precision, geo_source, floor, has_elevator, bathtub, furnished, charges_eur, '
            'bedrooms, description_full, description_content_state, description_length, '
            'description_sha256, description_full_text_evidence, description_succeeded_at, '
            'description_attempt_status, '
            'description_attempted_at, description_attempt_length, description_attempt_sha256, '
            'description_attempt_error, description_extractor_version, notes) '
            'values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
            'on conflict(source_site, source_id) do update set '
            'fetched_at=excluded.fetched_at, http_status=excluded.http_status, '
            "address=coalesce(nullif(trim(excluded.address), ''), listing_detail.address), "
            "street=coalesce(nullif(trim(excluded.street), ''), listing_detail.street), "
            "residence=coalesce(nullif(trim(excluded.residence), ''), listing_detail.residence), "
            "postal_code=coalesce(nullif(trim(excluded.postal_code), ''), listing_detail.postal_code), "
            "locality=coalesce(nullif(trim(excluded.locality), ''), listing_detail.locality), "
            'lat=coalesce(excluded.lat, listing_detail.lat), '
            'lon=coalesce(excluded.lon, listing_detail.lon), '
            "precision=case when lower(trim(coalesce(excluded.precision, ''))) in ('', 'inconnu') then listing_detail.precision else excluded.precision end, "
            "geo_source=case when lower(trim(coalesce(excluded.geo_source, ''))) in ('', 'aucun', 'inconnu') "
            'then listing_detail.geo_source else excluded.geo_source end, '
            "floor=coalesce(nullif(trim(excluded.floor), ''), listing_detail.floor), "
            'has_elevator=coalesce(excluded.has_elevator, listing_detail.has_elevator), '
            'bathtub=coalesce(excluded.bathtub, listing_detail.bathtub), '
            'furnished=coalesce(excluded.furnished, listing_detail.furnished), '
            'charges_eur=coalesce(excluded.charges_eur, listing_detail.charges_eur), '
            'bedrooms=coalesce(excluded.bedrooms, listing_detail.bedrooms), '
            "description_full=coalesce(nullif(trim(excluded.description_full), ''), listing_detail.description_full), "
            'description_content_state=excluded.description_content_state, '
            'description_length=excluded.description_length, '
            'description_sha256=excluded.description_sha256, '
            'description_full_text_evidence=excluded.description_full_text_evidence, '
            'description_succeeded_at=excluded.description_succeeded_at, '
            'description_attempt_status=excluded.description_attempt_status, '
            'description_attempted_at=excluded.description_attempted_at, '
            'description_attempt_length=excluded.description_attempt_length, '
            'description_attempt_sha256=excluded.description_attempt_sha256, '
            'description_attempt_error=excluded.description_attempt_error, '
            'description_extractor_version=excluded.description_extractor_version, '
            'notes=excluded.notes',
            (ss, si, attempted_at, status, rec.get('address'), rec.get('street'), rec.get('residence'),
             rec.get('postal_code'), rec.get('locality'), rec.get('lat'), rec.get('lon'),
             rec.get('precision'), rec.get('geo_source'), rec.get('floor'),
             rec.get('has_elevator'), rec.get('bathtub'), rec.get('furnished'),
             rec.get('charges_eur'), rec.get('bedrooms'), rec.get('description_full'),
             rec.get('description_content_state'), rec.get('description_length'),
             rec.get('description_sha256'), rec.get('description_full_text_evidence'),
             rec.get('description_succeeded_at'),
             rec.get('description_attempt_status'), rec.get('description_attempted_at'),
             rec.get('description_attempt_length'), rec.get('description_attempt_sha256'),
             rec.get('description_attempt_error'), rec.get('description_extractor_version'), note))
        c.commit()
        if i % 10 == 0 or i == len(rows):
            print('  %d/%d  ok=%d err=%d' % (i, len(rows), ok, err), flush=True)

    close_cdp()
    print('\nTERMINE: %d lues, %d erreurs' % (ok, err))


if __name__ == '__main__':
    try:
        main()
    finally:
        close_cdp()
