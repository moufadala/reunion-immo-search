#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recupere l'adresse exacte + GPS deja presents dans le JSON-LD locamoi,
jamais captes par scrape_locamoi() (qui ne garde que la ville). Ecrit dans
listing_detail comme pour bienici -- meme table, memes colonnes deja migrees.

Trouve le 28/07 : itemOffered.address.streetAddress et itemOffered.geo
(latitude/longitude) sont deja dans la reponse qu'on recupere a chaque
scrape de liste -- c'est la meilleure precision de localisation disponible
sur tout le projet, et elle etait jetee.
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, '/opt/data/projects/reunion-immo-search/scripts')
import realestate_multi_sources_scraper as m

DB = os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db')


def ensure_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS listing_detail (
        source_site TEXT NOT NULL, source_id TEXT NOT NULL, fetched_at TEXT NOT NULL,
        http_status INTEGER, address TEXT, street TEXT, residence TEXT, postal_code TEXT,
        locality TEXT, lat REAL, lon REAL, precision TEXT, geo_source TEXT, floor TEXT,
        has_elevator INTEGER, bathtub INTEGER, furnished INTEGER, charges_eur INTEGER,
        bedrooms INTEGER, description_full TEXT, notes TEXT,
        PRIMARY KEY (source_site, source_id))''')


def main(max_pages=7, delay=1.5):
    conn = sqlite3.connect(DB)
    ensure_table(conn)
    n_ok = 0
    n_seen = 0
    for page in range(1, max_pages + 1):
        url = 'https://locamoi.fr/location/appartement/la-reunion'
        if page > 1:
            url += f'?page={page}'
        try:
            text, _ = m.fetch(url)
        except Exception:
            break
        time.sleep(delay)
        mm = re.search(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.S)
        if not mm:
            break
        try:
            obj = json.loads(mm.group(1))
        except Exception:
            break
        items = obj.get('mainEntity', {}).get('itemListElement', [])
        if not items:
            break
        for it in items:
            n_seen += 1
            item = it.get('item', {})
            offers = item.get('offers', {})
            offered = offers.get('itemOffered', {})
            url_i = item.get('url') or offers.get('url')
            if not url_i:
                continue
            sid = url_i.rstrip('/').split('-')[-1]
            addr = offered.get('address', {}) if isinstance(offered.get('address'), dict) else {}
            geo = offered.get('geo', {}) if isinstance(offered.get('geo'), dict) else {}
            street = addr.get('streetAddress')
            lat = geo.get('latitude')
            lon = geo.get('longitude')
            if not street and lat is None:
                continue
            now = datetime.now(timezone.utc).isoformat()
            conn.execute('''INSERT INTO listing_detail
                (source_site, source_id, fetched_at, http_status, address, lat, lon, precision, geo_source)
                VALUES ('locamoi', ?, ?, 200, ?, ?, ?, ?, 'jsonld')
                ON CONFLICT(source_site, source_id) DO UPDATE SET
                    fetched_at=excluded.fetched_at, http_status=200,
                    address=excluded.address, lat=excluded.lat, lon=excluded.lon,
                    precision=excluded.precision, geo_source='jsonld' ''',
                (sid, now, street, lat, lon, 'adresse_exacte' if street else 'point_carte'))
            n_ok += 1
        conn.commit()
    conn.close()
    print(f'pages lues jusqu\'a {page}, items vus={n_seen}, avec adresse/geo={n_ok}')


if __name__ == '__main__':
    main()
