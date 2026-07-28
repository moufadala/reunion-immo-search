#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rafraîchit uniquement les galeries Domimmo publiques qui sont mono-photo.

Contexte 28/07 : certaines annonces Domimmo ont un raw JSON avec URLs Keldom
périmées (404). On ne re-scrape pas le catalogue : on cible les annonces Domimmo
présentes dans le feed public courant, puis on tente :
1) lookup exact `keldom.com/api/domimmo/offers?id=<source_id>` ;
2) fallback chemin blob `/photos/1024/` pour les raws legacy `/120/` ou `/400/`.

Le script écrit seulement `listing_detail.photo_urls` quand au moins deux URLs
image répondent 200. Il ne touche pas `http_status`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DB = os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db')
ROOT = Path(os.environ.get('IMMO_PROJECT', '/opt/data/projects/reunion-immo-search'))
APP = Path(os.environ.get('IMMO_APP_PATH', str(ROOT / 'artifacts/app')))
OUT = os.environ.get('IMMO_DOMIMMO_PHOTO_REFRESH_REPORT', '/opt/data/artifacts/domimmo_photo_refresh_report.json')
TIMEOUT = float(os.environ.get('IMMO_DOMIMMO_PHOTO_REFRESH_TIMEOUT', '10'))
MAX_TARGETS = int(os.environ.get('IMMO_DOMIMMO_PHOTO_REFRESH_MAX', '30'))
SLEEP = float(os.environ.get('IMMO_DOMIMMO_PHOTO_REFRESH_SLEEP', '0.15'))
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126 Safari/537.36')
CTX = ssl.create_default_context()


def request(url: str, method: str = 'GET') -> tuple[int | None, str, str]:
    req = urllib.request.Request(url, method=method, headers={
        'User-Agent': UA,
        'Accept': 'application/json,image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        'Referer': 'https://www.domimmo.com/',
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            body = r.read(1_500_000).decode('utf-8', 'replace') if method == 'GET' else ''
            return r.status, r.headers.get('content-type') or '', body
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('content-type') if e.headers else '', ''
    except Exception as e:
        return None, '', repr(e)


def clean_urls(urls: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        if not isinstance(raw, str):
            continue
        u = raw.replace('\\/', '/').replace('&amp;', '&').strip()
        if not u.startswith(('http://', 'https://')):
            continue
        if '/pro/photos/' in urllib.parse.urlparse(u).path:
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def keldom_1024_variants(url: str) -> list[str]:
    variants = [url]
    for marker in ('/photos/400/', '/photos/120/'):
        if marker in url:
            variants.insert(0, url.replace(marker, '/photos/1024/'))
    return clean_urls(variants)


def validate_images(urls: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    valid: list[str] = []
    probes: list[dict[str, Any]] = []
    for u in clean_urls(urls):
        tried = []
        for cand in keldom_1024_variants(u):
            status, ctype, _ = request(cand, 'HEAD')
            tried.append({'url': cand, 'status': status, 'content_type': ctype})
            time.sleep(SLEEP)
            if status == 200 and 'image' in ctype.lower():
                if cand not in valid:
                    valid.append(cand)
                break
        probes.append({'source_url': u, 'tried': tried})
    return valid, probes


def raw_photo_urls(raw_path: str | None, image_url: str | None) -> list[str]:
    urls: list[Any] = []
    if image_url:
        urls.append(image_url)
    if raw_path and Path(raw_path).is_file():
        try:
            raw = json.loads(Path(raw_path).read_text(encoding='utf-8'))
        except Exception:
            raw = {}
        item = raw.get('raw') if isinstance(raw.get('raw'), dict) else raw
        if isinstance(item, dict):
            photos = item.get('photos')
            if isinstance(photos, list):
                urls.extend(photos)
            if item.get('imageSrc'):
                urls.insert(0, item.get('imageSrc'))
        if isinstance(raw, dict) and raw.get('image'):
            urls.insert(0, raw.get('image'))
    return clean_urls(urls)


def api_photos(source_id: str) -> tuple[list[str], dict[str, Any]]:
    url = 'https://www.keldom.com/api/domimmo/offers?' + urllib.parse.urlencode({'id': source_id})
    status, ctype, body = request(url, 'GET')
    meta: dict[str, Any] = {'url': url, 'status': status, 'content_type': ctype}
    try:
        payload = json.loads(body)
    except Exception as e:
        meta['json_error'] = repr(e)
        return [], meta
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get('items') if isinstance(payload.get('items'), list) else payload.get('data') if isinstance(payload.get('data'), list) else [payload]
    else:
        items = []
    meta['items_count'] = len(items)
    for item in items:
        if isinstance(item, dict) and str(item.get('id')) == source_id:
            photos = item.get('photos') if isinstance(item.get('photos'), list) else []
            meta['matched'] = True
            meta['photos_count'] = len(photos)
            return clean_urls(photos), meta
    meta['matched'] = False
    return [], meta


def ensure_photo_col(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute('PRAGMA table_info(listing_detail)')}
    if 'photo_urls' not in cols:
        conn.execute('ALTER TABLE listing_detail ADD COLUMN photo_urls TEXT')


def main() -> int:
    feed_path = APP / 'feed.json'
    feed = json.loads(feed_path.read_text(encoding='utf-8'))
    # Après enrich_listing_photos.py, la DB peut revenir aux URLs raw périmées.
    # Le feed public précédent peut déjà afficher ces annonces en multi-photo,
    # donc filtrer ici sur <=1 image rendrait le build non idempotent. On reste
    # borné au feed public Domimmo (≈ quelques dizaines), pas au catalogue entier.
    targets = [x for x in feed.get('listings', []) if x.get('source') == 'domimmo']
    targets = targets[:MAX_TARGETS]
    conn = sqlite3.connect(DB)
    ensure_photo_col(conn)
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    report: dict[str, Any] = {'ok': True, 'targets': len(targets), 'updated': 0, 'items': []}
    for x in targets:
        sid = str(x['id']).split(':', 1)[1]
        row = conn.execute('''select source_id, raw_json_path, image_url, title, city
                                from rental_listings
                               where source_site='domimmo' and source_id=?''', (sid,)).fetchone()
        if not row:
            continue
        api_urls, api_meta = api_photos(sid)
        candidates = api_urls or raw_photo_urls(row[1], row[2])
        valid, probes = validate_images(candidates)
        item = {'source_id': sid, 'title': row[3], 'feed_images_before': len(x.get('images') or []),
                'api': api_meta, 'candidate_count': len(candidates), 'valid_count': len(valid),
                'valid_sample': valid[:5], 'probe_sample': probes[:3]}
        if len(valid) >= 2:
            conn.execute('''insert into listing_detail (source_site, source_id, fetched_at, photo_urls)
                            values ('domimmo', ?, ?, ?)
                            on conflict(source_site, source_id) do update set photo_urls=excluded.photo_urls''',
                         (sid, now, json.dumps(valid, ensure_ascii=False)))
            report['updated'] += 1
            item['updated'] = True
        else:
            item['updated'] = False
        report['items'].append(item)
        time.sleep(SLEEP)
    conn.commit()
    conn.close()
    Path(OUT).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'targets': report['targets'], 'updated': report['updated'], 'report': OUT}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
