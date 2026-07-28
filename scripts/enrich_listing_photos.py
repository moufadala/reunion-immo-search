#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extrait les galeries photo déjà présentes dans raw_json_path.

Aucune requête réseau : ce script relit les JSON déjà sauvegardés par les
scrapers, puis écrit la liste ordonnée complète dans listing_detail.photo_urls.
Il suit le même modèle que enrich_locamoi_geo.py : enrichissement DB
idempotent, keyé par (source_site, source_id), sans toucher rental_listings.

Sources confirmées le 28/07 :
- domimmo : raw.raw.photos contient plusieurs URLs Keldom ;
- bienici : photos[].url contient plusieurs URLs Bien'ici ;
- locamoi : raw actuel mono-photo seulement, donc volontairement ignoré.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DB = os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db')
SOURCES = ('domimmo', 'bienici')


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS listing_detail (
        source_site TEXT NOT NULL, source_id TEXT NOT NULL, fetched_at TEXT NOT NULL,
        http_status INTEGER, address TEXT, street TEXT, residence TEXT, postal_code TEXT,
        locality TEXT, lat REAL, lon REAL, precision TEXT, geo_source TEXT, floor TEXT,
        has_elevator INTEGER, bathtub INTEGER, furnished INTEGER, charges_eur INTEGER,
        bedrooms INTEGER, description_full TEXT, notes TEXT,
        PRIMARY KEY (source_site, source_id))''')
    cols = {r[1] for r in conn.execute('PRAGMA table_info(listing_detail)')}
    if 'photo_urls' not in cols:
        conn.execute('ALTER TABLE listing_detail ADD COLUMN photo_urls TEXT')


def clean_urls(urls: Sequence[str | None]) -> list[str]:
    """URLs HTTP propres, ordonnées, sans doublon ni logo agence connu."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        if not raw or not isinstance(raw, str):
            continue
        u = raw.replace('\\/', '/').replace('&amp;', '&').strip()
        if not u.startswith(('http://', 'https://')):
            continue
        parsed = urlparse(u)
        # Domimmo/Keldom expose parfois une URL tronquée .../photos/400/
        # et des logos agence sous /pro/photos/ ; ce ne sont pas des photos bien.
        if not Path(parsed.path.rstrip('/')).name:
            continue
        if '/pro/photos/' in parsed.path:
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def extract_photo_urls(source: str, raw: dict) -> list[str]:
    if source == 'domimmo':
        item = raw.get('raw') if isinstance(raw.get('raw'), dict) else raw
        photos = item.get('photos') if isinstance(item, dict) else []
        urls: list[str | None] = []
        if isinstance(photos, list):
            for photo in photos:
                if isinstance(photo, dict):
                    urls.append(photo.get('src') or photo.get('url') or photo.get('imageSrc'))
                elif isinstance(photo, str):
                    urls.append(photo)
        if isinstance(item, dict):
            urls.insert(0, item.get('imageSrc') or item.get('image'))
        if isinstance(raw, dict):
            urls.insert(0, raw.get('image'))
        return clean_urls(urls)

    if source == 'bienici':
        photos = raw.get('photos') if isinstance(raw, dict) else []
        urls = []
        if isinstance(photos, list):
            for photo in photos:
                if isinstance(photo, dict):
                    # url = CDN Bien'ici déjà utilisé comme image principale.
                    # url_photo peut pointer vers l'origine partenaire : repli seulement.
                    urls.append(photo.get('url') or photo.get('url_photo') or photo.get('fullUrl') or photo.get('src'))
                elif isinstance(photo, str):
                    urls.append(photo)
        return clean_urls(urls)

    return []


def main() -> int:
    conn = sqlite3.connect(DB)
    ensure_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    stats: Counter[str] = Counter()
    examples: dict[str, list[dict]] = {s: [] for s in SOURCES}

    rows = conn.execute('''SELECT source_site, source_id, raw_json_path, image_url
                             FROM rental_listings
                            WHERE source_site IN ('domimmo', 'bienici')
                              AND raw_json_path IS NOT NULL AND raw_json_path <> ''
                         ORDER BY source_site, source_id''').fetchall()

    for source, sid, raw_path, image_url in rows:
        path = Path(raw_path)
        if not path.is_file():
            stats[f'{source}:raw_missing'] += 1
            continue
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            stats[f'{source}:raw_unreadable'] += 1
            continue
        urls = extract_photo_urls(source, raw)
        if image_url and image_url not in urls:
            urls.insert(0, image_url)
            urls = clean_urls(urls)
        if len(urls) < 2:
            stats[f'{source}:mono_or_empty'] += 1
            continue
        # Ne pas marquer http_status=200 ici : ce script relit un JSON déjà
        # sauvegardé, il ne prouve pas que la page détail a été lue en entier.
        # `export_feed.py` utilise http_status=200 pour afficher detail_read.
        conn.execute('''INSERT INTO listing_detail
            (source_site, source_id, fetched_at, photo_urls)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_site, source_id) DO UPDATE SET
                photo_urls=excluded.photo_urls''',
            (source, str(sid), now, json.dumps(urls, ensure_ascii=False)))
        stats[f'{source}:multi_written'] += 1
        if len(examples[source]) < 5:
            examples[source].append({'source_id': str(sid), 'count': len(urls), 'first_urls': urls[:4]})

    conn.commit()
    conn.close()
    out = {'ok': True, 'db': DB, 'stats': dict(sorted(stats.items())), 'examples': examples}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
