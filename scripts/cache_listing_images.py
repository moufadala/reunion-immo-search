#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures as cf
import hashlib
import json
import mimetypes
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = Path('/opt/data/projects/reunion-immo-search')
APP = Path(os.environ.get('IMMO_APP_PATH', str(BASE / 'artifacts' / 'app')))
JSON_PATH = APP / 'listings.json'
HTML_PATH = APP / 'index.html'
THUMBS = APP / 'thumbs'
MANIFEST = APP / 'thumbs_manifest.json'
REPORT = Path(os.environ.get('IMMO_PHOTO_REPORT', str(BASE / 'artifacts' / 'photo-cache-report.md')))
MAX_WORKERS = int(os.environ.get('PHOTO_WORKERS', '8'))
TIMEOUT = float(os.environ.get('PHOTO_TIMEOUT', '18'))
MIN_BYTES = int(os.environ.get('PHOTO_MIN_BYTES', '1200'))
MAX_BYTES = int(os.environ.get('PHOTO_MAX_BYTES', str(8 * 1024 * 1024)))
FETCH_MISSING = os.environ.get('PHOTO_FETCH_MISSING', '1') not in {'0', 'false', 'False', 'no', 'NO'}

UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
HEADERS = {
    'User-Agent': UA,
    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
    'Cache-Control': 'no-cache',
}
EXT_BY_CT = {
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
    'image/gif': '.gif',
    'image/avif': '.avif',
}
BAD_HOST_PARTS = ('logo', 'placeholder')


def load_data() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = json.loads(JSON_PATH.read_text())
    if not isinstance(raw, dict) or 'listings' not in raw:
        raise SystemExit(f'Unexpected JSON shape in {JSON_PATH}')
    return raw, raw['listings']


def safe_ext(url: str, content_type: str | None) -> str:
    ct = (content_type or '').split(';', 1)[0].strip().lower()
    if ct in EXT_BY_CT:
        return EXT_BY_CT[ct]
    path = urllib.parse.urlparse(url).path.lower()
    ext = Path(path).suffix
    if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'):
        return '.jpg' if ext == '.jpeg' else ext
    guess = mimetypes.guess_extension(ct or '')
    return guess or '.jpg'


def local_name(url: str, ext: str = '.jpg') -> str:
    h = hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]
    return h + ext


def existing_for_url(url: str) -> Path | None:
    stem = hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]
    for p in THUMBS.glob(stem + '.*'):
        if p.is_file() and p.stat().st_size >= MIN_BYTES:
            return p
    return None


def fetch_one(url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if not parsed.scheme.startswith('http'):
        return {'url': url, 'ok': False, 'reason': 'non_http'}
    old = existing_for_url(url)
    if old:
        return {'url': url, 'ok': True, 'cached': True, 'path': str(old), 'bytes': old.stat().st_size, 'content_type': None}
    if not FETCH_MISSING:
        return {'url': url, 'ok': False, 'reason': 'missing_fetch_disabled'}

    req_headers = dict(HEADERS)
    if 'seloger.com' in host:
        req_headers['Referer'] = 'https://www.seloger.com/'
    elif 'citya.com' in host:
        req_headers['Referer'] = 'https://www.citya.com/'
    elif 'fnaim.re' in host:
        req_headers['Referer'] = 'https://www.fnaim.re/'
    elif 'ofim.fr' in host:
        req_headers['Referer'] = 'https://www.ofim.fr/'
    elif 'immo974.com' in host:
        req_headers['Referer'] = 'https://www.immo974.com/'
    elif 'bienici.com' in host:
        req_headers['Referer'] = 'https://www.bienici.com/'
    elif 'zimo' in url or 'e-xiste.com' in host:
        req_headers['Referer'] = 'https://www.zimo.fr/'

    last_err = ''
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=req_headers, method='GET')
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as resp:
                status = getattr(resp, 'status', 200)
                ct = resp.headers.get('Content-Type', '')
                if status >= 400:
                    return {'url': url, 'ok': False, 'reason': f'http_{status}', 'content_type': ct}
                data = resp.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    return {'url': url, 'ok': False, 'reason': 'too_large', 'bytes': len(data), 'content_type': ct}
                if len(data) < MIN_BYTES:
                    return {'url': url, 'ok': False, 'reason': 'too_small', 'bytes': len(data), 'content_type': ct}
                cts = ct.split(';', 1)[0].lower().strip()
                if cts and not cts.startswith('image/'):
                    # Some CDNs lie rarely, but text/html is almost always a block page.
                    return {'url': url, 'ok': False, 'reason': 'non_image_content_type', 'bytes': len(data), 'content_type': ct}
                ext = safe_ext(url, ct)
                out = THUMBS / local_name(url, ext)
                tmp = out.with_suffix(out.suffix + '.tmp')
                tmp.write_bytes(data)
                tmp.replace(out)
                return {'url': url, 'ok': True, 'cached': False, 'path': str(out), 'bytes': len(data), 'content_type': ct}
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
            time.sleep(0.6 * attempt)
    return {'url': url, 'ok': False, 'reason': 'exception', 'error': last_err}


def main() -> int:
    THUMBS.mkdir(parents=True, exist_ok=True)
    raw, listings = load_data()
    urls = sorted({str(u) for x in listings for u in ((x.get('image_urls') or [x.get('image_url')]) if isinstance(x, dict) else []) if u})
    print(f'PHOTO_CACHE_START listings={len(listings)} unique_urls={len(urls)} workers={MAX_WORKERS}')

    results: list[dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_one, u): u for u in urls}
        done = 0
        for fut in cf.as_completed(futs):
            r = fut.result()
            results.append(r)
            done += 1
            if done % 25 == 0 or done == len(urls):
                ok = sum(1 for x in results if x.get('ok'))
                print(f'progress {done}/{len(urls)} ok={ok} fail={done-ok}', flush=True)

    by_url = {r['url']: r for r in results}
    ok_by_url = {u: r for u, r in by_url.items() if r.get('ok') and r.get('path')}

    patched = 0
    gallery_patched = 0
    for it in listings:
        gallery = it.get('image_urls') or ([it.get('image_url')] if it.get('image_url') else [])
        local_gallery = []
        for u in gallery:
            r = ok_by_url.get(u)
            if r:
                rel = Path(r['path']).relative_to(APP).as_posix()
                if rel not in local_gallery:
                    local_gallery.append(rel)
        if local_gallery:
            it['local_image_url'] = local_gallery[0]
            it['local_image_urls'] = local_gallery
            it['photo_cached'] = True
            patched += 1
            if len(local_gallery) > 1:
                gallery_patched += 1
        else:
            it['local_image_url'] = ''
            it['local_image_urls'] = []
            it['photo_cached'] = False

    raw.setdefault('meta', {})['photo_cache'] = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'unique_urls': len(urls),
        'unique_ok': len(ok_by_url),
        'listing_photo_cached': patched,
        'listing_gallery_cached': gallery_patched,
        'thumbs_dir': '/thumbs/',
    }
    JSON_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2))

    # The app is static and embeds the same data in index.html for instant load.
    # Patch that embedded JSON too, otherwise the browser keeps using external image_url.
    if HTML_PATH.exists():
        html = HTML_PATH.read_text()
        marker_start = '<script id="embeddedData" type="application/json">'
        marker_end = '</script>'
        start = html.find(marker_start)
        if start >= 0:
            data_start = start + len(marker_start)
            end = html.find(marker_end, data_start)
            if end >= 0:
                compact_payload = dict(raw)
                suspects_path = APP / 'suspects.json'
                if suspects_path.exists() and 'suspects' not in compact_payload:
                    try:
                        compact_payload['suspects'] = json.loads(suspects_path.read_text()).get('suspects', [])
                    except Exception:
                        compact_payload['suspects'] = []
                compact = json.dumps(compact_payload, ensure_ascii=False, separators=(',', ':'))
                html = html[:data_start] + compact + html[end:]
                HTML_PATH.write_text(html)

    manifest = {
        'generated_at': raw['meta']['photo_cache']['generated_at'],
        'unique_urls': len(urls),
        'unique_ok': len(ok_by_url),
        'listing_photo_cached': patched,
        'results': sorted(results, key=lambda x: (not x.get('ok'), x.get('url',''))),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    from collections import Counter
    fail_reasons = Counter(r.get('reason','unknown') for r in results if not r.get('ok'))
    hosts = Counter(urllib.parse.urlparse(r['url']).netloc for r in results if r.get('ok'))
    report = [
        '# Photo cache V5', '',
        f'- Listings: {len(listings)}',
        f'- URLs uniques: {len(urls)}',
        f'- URLs téléchargées OK: {len(ok_by_url)}',
        f'- Annonces avec photo locale: {patched}/{len(listings)}',
        f'- Annonces avec galerie locale (>1): {gallery_patched}',
        f'- Manifest: `{MANIFEST}`',
        '', '## Échecs par raison',
    ]
    if fail_reasons:
        report += [f'- {k}: {v}' for k, v in fail_reasons.most_common()]
    else:
        report += ['- Aucun']
    report += ['', '## Hosts OK principaux']
    report += [f'- {h}: {n}' for h, n in hosts.most_common(30)]
    REPORT.write_text('\n'.join(report) + '\n')

    print(f'PHOTO_CACHE_DONE unique_ok={len(ok_by_url)}/{len(urls)} listing_cached={patched}/{len(listings)} gallery_cached={gallery_patched}')
    if fail_reasons:
        print('FAIL_REASONS', dict(fail_reasons))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
