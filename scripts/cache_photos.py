#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rapatrie les photos des annonces CHEZ NOUS, et n'en depend plus jamais.

Regle posee par Moufadal le 2026-07-27. La preuve du besoin est deja payee :
les photos zimo ont ete SUPPRIMEES a la source (placeholder de 266 octets,
original sur leur S3 -> 404), et toutes les cartes zimo affichaient
"Photo indisponible" alors que 79 vignettes dormaient deja sur notre disque.

Ce script est idempotent et NON DESTRUCTIF :
  - il ne supprime jamais un fichier existant ;
  - un fichier deja present n'est pas retelecharge (le nom est le hash de l'URL) ;
  - il ecrit un manifeste que export_feed.py lit ; le feed ne contient plus
    d'URL distante dans le champ `image`.

Piege deja paye (handoff 27/07) : marteler un hote qui bloque est le meilleur
moyen de se faire bannir. Le parcours est donc en ROUND-ROBIN entre portails,
avec un delai par hote, jamais en rafale sur un seul.

Sorties :
  artifacts/app/thumbs/<sha256(url)[:24]>.<ext>
  artifacts/app/photos_manifest.json
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get('IMMO_ROOT', '/opt/data/projects/reunion-immo-search'))
DB = os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db')
APP = Path(os.environ.get('IMMO_APP_PATH', str(ROOT / 'artifacts' / 'app')))
THUMBS = APP / 'thumbs'
MANIFEST = APP / 'photos_manifest.json'

TIMEOUT = float(os.environ.get('PHOTO_TIMEOUT', '20'))
MIN_BYTES = int(os.environ.get('PHOTO_MIN_BYTES', '1200'))   # tue le placeholder zimo (266 o)
MAX_BYTES = int(os.environ.get('PHOTO_MAX_BYTES', str(8 * 1024 * 1024)))
DELAI_HOTE = float(os.environ.get('PHOTO_HOST_DELAY', '1.2'))  # secondes entre 2 hits d'un meme hote
MAX_ECHECS_HOTE = int(os.environ.get('PHOTO_HOST_MAX_FAILS', '8'))  # on lache un hote qui bloque
OFFLINE = os.environ.get('PHOTO_OFFLINE', '0') not in {'0', '', 'false', 'no'}

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 '
      '(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1')
HEADERS = {
    'User-Agent': UA,
    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
}
# Plusieurs portails exigent un Referer coherent pour servir leurs medias.
REFERERS = {
    'seloger.com': 'https://www.seloger.com/',
    'citya.com': 'https://www.citya.com/',
    'fnaim.re': 'https://www.fnaim.re/',
    'ofim.fr': 'https://www.ofim.fr/',
    'immo974.com': 'https://www.immo974.com/',
    'bienici.com': 'https://www.bienici.com/',
    'zimo.fr': 'https://www.zimo.fr/',
    'e-xiste.com': 'https://www.zimo.fr/',
    'superimmo.com': 'https://www.superimmo.com/',
    'domimmo.com': 'https://www.domimmo.com/',
    'locamoi.com': 'https://www.locamoi.com/',
}
EXT_BY_CT = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png',
    'image/webp': '.webp', 'image/gif': '.gif', 'image/avif': '.avif',
}


def stem(url: str) -> str:
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]


def ext_pour(url: str, content_type: str | None) -> str:
    ct = (content_type or '').split(';', 1)[0].strip().lower()
    if ct in EXT_BY_CT:
        return EXT_BY_CT[ct]
    suf = Path(urllib.parse.urlparse(url).path.lower()).suffix
    if suf in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'):
        return '.jpg' if suf == '.jpeg' else suf
    return mimetypes.guess_extension(ct or '') or '.jpg'


def fichier_local(url: str) -> Path | None:
    """Une copie exploitable existe-t-elle deja ? (assez grosse pour ne pas
    etre un placeholder)."""
    s = stem(url)
    for p in sorted(THUMBS.glob(s + '.*')):
        if p.is_file() and p.stat().st_size >= MIN_BYTES:
            return p
    return None


def referer_pour(url: str) -> str | None:
    host = urllib.parse.urlparse(url).netloc.lower()
    for cle, ref in REFERERS.items():
        if cle in host or cle in url:
            return ref
    return None


def telecharger(url: str) -> tuple[Path | None, str]:
    """-> (chemin, raison). Ne leve jamais."""
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme.startswith('http'):
        return None, 'non_http'
    h = dict(HEADERS)
    ref = referer_pour(url)
    if ref:
        h['Referer'] = ref
    try:
        req = urllib.request.Request(url, headers=h, method='GET')
        with urllib.request.urlopen(req, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as r:
            code = getattr(r, 'status', 200)
            ct = r.headers.get('Content-Type', '')
            if code >= 400:
                return None, 'http_%s' % code
            data = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        return None, 'http_%s' % e.code
    except Exception as e:                                   # noqa: BLE001
        return None, type(e).__name__.lower()

    if len(data) > MAX_BYTES:
        return None, 'trop_gros'
    if len(data) < MIN_BYTES:
        # c'est le cas zimo : un "placeholder" de quelques centaines d'octets
        return None, 'trop_petit_%do' % len(data)
    if not data[:16].strip():
        return None, 'vide'

    p = THUMBS / (stem(url) + ext_pour(url, ct))
    tmp = p.with_suffix(p.suffix + '.part')
    tmp.write_bytes(data)
    tmp.replace(p)                       # ecriture atomique : jamais de demi-fichier servi
    return p, 'ok'


def main() -> int:
    THUMBS.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    c.row_factory = sqlite3.Row

    # actives d'abord, puis les plus recemment vues : si un hote bloque, on aura
    # au moins servi ce qui est affiche aujourd'hui.
    lignes = list(c.execute("""
        select source_site, source_id, image_url, is_active, seen_last_at
          from rental_listings
         where image_url is not null and image_url <> ''
         order by is_active desc, seen_last_at desc
    """))

    manifeste: dict[str, dict] = {}
    a_chercher: list[sqlite3.Row] = []
    deja = 0
    for r in lignes:
        cle = '%s:%s' % (r['source_site'], r['source_id'])
        p = fichier_local(r['image_url'])
        if p:
            manifeste[cle] = {
                'local': '/thumbs/' + p.name,
                'octets': p.stat().st_size,
                'source_url': r['image_url'],
                'origine': 'cache',
            }
            deja += 1
        else:
            a_chercher.append(r)

    print('deja en local : %d / %d  (%d a telecharger)'
          % (deja, len(lignes), len(a_chercher)))

    echecs: dict[str, int] = defaultdict(int)
    raisons: dict[str, int] = defaultdict(int)
    recuperees = 0

    if a_chercher and not OFFLINE:
        # round-robin : une file par hote, on pioche a tour de role.
        files: dict[str, deque] = defaultdict(deque)
        for r in a_chercher:
            files[urllib.parse.urlparse(r['image_url']).netloc.lower()].append(r)
        dernier_hit: dict[str, float] = {}
        hotes = deque(files.keys())
        print('hotes : ' + ', '.join('%s(%d)' % (h, len(files[h])) for h in hotes))

        while hotes:
            hote = hotes[0]
            hotes.rotate(-1)
            file = files[hote]
            if not file:
                hotes.remove(hote)
                continue
            if echecs[hote] >= MAX_ECHECS_HOTE:
                # cet hote nous ferme la porte : on ne s'acharne pas.
                raisons['%s:abandon_hote' % hote] += len(file)
                file.clear()
                hotes.remove(hote)
                continue
            attente = DELAI_HOTE - (time.time() - dernier_hit.get(hote, 0))
            if attente > 0 and len(hotes) <= 1:
                time.sleep(attente)
            r = file.popleft()
            dernier_hit[hote] = time.time()
            p, raison = telecharger(r['image_url'])
            raisons['%s:%s' % (hote, raison)] += 1
            if p:
                recuperees += 1
                echecs[hote] = 0
                manifeste['%s:%s' % (r['source_site'], r['source_id'])] = {
                    'local': '/thumbs/' + p.name,
                    'octets': p.stat().st_size,
                    'source_url': r['image_url'],
                    'origine': 'telecharge',
                }
            else:
                echecs[hote] += 1

    manquantes = [r for r in lignes
                  if '%s:%s' % (r['source_site'], r['source_id']) not in manifeste]
    actives_manquantes = [r for r in manquantes if r['is_active']]

    sortie = {
        'genere_le': datetime.now(timezone.utc).isoformat(),
        'total_annonces': len(lignes),
        'avec_photo_locale': len(manifeste),
        'telechargees_ce_run': recuperees,
        'sans_photo': len(manquantes),
        'sans_photo_actives': len(actives_manquantes),
        'raisons': dict(sorted(raisons.items(), key=lambda kv: -kv[1])),
        'photos': manifeste,
    }
    MANIFEST.write_text(json.dumps(sortie, ensure_ascii=False, indent=1), encoding='utf-8')

    print('manifeste : %s' % MANIFEST)
    print('  photos locales   : %d / %d' % (len(manifeste), len(lignes)))
    print('  recuperees ce run: %d' % recuperees)
    print('  sans photo       : %d (dont %d actives)'
          % (len(manquantes), len(actives_manquantes)))
    for k, v in list(sortie['raisons'].items())[:15]:
        print('    %-45s %d' % (k, v))
    return 0


if __name__ == '__main__':
    sys.exit(main())
