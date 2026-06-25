#!/usr/bin/env python3
from __future__ import annotations
import argparse
import concurrent.futures as cf
import hashlib
import html
import json
import os
import re
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = Path('/opt/data/projects/reunion-immo-search')
_parser = argparse.ArgumentParser()
_parser.add_argument('--db', default=os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db'))
_parser.add_argument('--app', default=os.environ.get('IMMO_APP_PATH', str(BASE / 'artifacts' / 'app')))
_parser.add_argument('--report', default=os.environ.get('IMMO_GALLERY_REPORT', str(BASE / 'artifacts' / 'gallery-enrichment-report.md')))
_parser.add_argument('--manifest', default=os.environ.get('IMMO_GALLERY_MANIFEST', str(BASE / 'artifacts' / 'gallery-enrichment-manifest.json')))
_args = _parser.parse_args()
APP = Path(_args.app)
JSON_PATH = APP / 'listings.json'
DB = Path(_args.db)
OUT = Path(_args.report)
DETAIL_CACHE = BASE / 'artifacts' / 'gallery_detail_cache'
MANIFEST = Path(_args.manifest)

IMG_EXT_RE = re.compile(r'https?://[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp|avif)(?:\?[^\s"\'<>]*)?', re.I)
SRCSET_URL_RE = re.compile(r'(https?://[^\s,]+)\s+(?:\d+[wx])', re.I)
CSS_URL_RE = re.compile(r'url\(["\']?(https?://[^)"\']+)["\']?\)', re.I)
UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
    'Cache-Control': 'no-cache',
}
TIMEOUT = float(os.environ.get('GALLERY_DETAIL_TIMEOUT', '12'))
WORKERS = int(os.environ.get('GALLERY_WORKERS', '8'))
MIN_HTML_BYTES = 700

BAD_URL_PARTS = (
    'logo', 'favicon', 'sprite', 'placeholder', 'vesta-z98lw1g', 'picto', 'marker',
    'avatar', 'agency', 'agence', 'map-marker', 'carte_', 'carte-', 'loader', 'spinner',
    'facebook', 'twitter', 'instagram', 'linkedin', 'youtube', 'apple-touch-icon',
)
GOOD_SOURCE_HOST_PARTS = (
    'herren-immobilier.com/images', 'img.netty.immo', 'media.studio-net.fr/biens',
    'fnaim.re/images/biens', '97immo.com/images/immo', 'keldom.blob.core.windows.net/domimmo',
    'photos.pagesimmo.com', 'photos5.pagesimmo.com', 'mms.seloger.com', 'file.bienici.com',
    'ofim.fr', 'immo974.com', 'zimo.fr', 'e-xiste.com', 'citya.com',
)
DETAIL_SOURCES = {
    'locamoi', '97immo', 'fnaim', 'domimmo', 'alter', 'immo974', 'ofim', 'ofim_rss', 'citya', 'zimo', 'bienici'
}


def norm_url(u: str) -> str:
    u = html.unescape((u or '').strip().replace('\\/', '/'))
    if u.startswith('//'):
        u = 'https:' + u
    # Strip common JSON/HTML escape tail after regex capture.
    return u.rstrip('\\')


def likely_listing_image(u: str, source: str = '') -> bool:
    if not u:
        return False
    low = urllib.parse.unquote(u).lower()
    if not low.startswith(('http://', 'https://')):
        return False
    if not re.search(r'\.(jpg|jpeg|png|webp|avif)(\?|$)', low):
        return False
    if any(b in low for b in BAD_URL_PARTS):
        return False
    # 97immo detail pages contain many Reunion map png files. Keep only property folders.
    if source == '97immo' and '97immo.com/images/immo/' not in low:
        return False
    # Locamoi/Rentola exposes both original remote URLs and Rentola CDN thumbnails. Prefer originals.
    if 'img2.rentola.com' in low and 'https%3a%2f%2f' in low:
        return False
    return True


def walk_images(o: Any, source: str = '') -> list[str]:
    urls: list[str] = []
    if isinstance(o, dict):
        for k, v in o.items():
            kl = k.lower()
            if isinstance(v, str):
                if any(t in kl for t in ('photo', 'image', 'picture', 'media', 'url', 'srcset')):
                    urls += IMG_EXT_RE.findall(v)
                    urls += SRCSET_URL_RE.findall(v)
                    urls += CSS_URL_RE.findall(v)
                    if v.startswith(('http://', 'https://', '//')) and re.search(r'\.(jpg|jpeg|png|webp|avif)(\?|$)', v, re.I):
                        urls.append(v)
            else:
                urls += walk_images(v, source)
    elif isinstance(o, list):
        for v in o:
            urls += walk_images(v, source)
    elif isinstance(o, str):
        urls += IMG_EXT_RE.findall(o)
        urls += SRCSET_URL_RE.findall(o)
        urls += CSS_URL_RE.findall(o)
    return dedupe_images(urls, source)


def collect_jsonld_images(obj: Any, current_url: str, source: str) -> list[str]:
    """Return images only from schema.org objects that describe the current listing."""
    urls: list[str] = []
    cur_path = urllib.parse.urlparse(current_url).path.rstrip('/')
    if isinstance(obj, dict):
        typ = obj.get('@type') or obj.get('type') or ''
        typ_text = ' '.join(typ) if isinstance(typ, list) else str(typ)
        obj_url = str(obj.get('url') or obj.get('@id') or '')
        obj_path = urllib.parse.urlparse(obj_url).path.rstrip('/') if obj_url else ''
        is_listing = any(t in typ_text.lower() for t in ('realestatelisting', 'apartment', 'house', 'residence', 'offer'))
        url_matches = bool(cur_path and obj_path and (cur_path == obj_path or cur_path in obj_path or obj_path in cur_path))
        if is_listing or url_matches:
            imgs = obj.get('image') or obj.get('photo') or obj.get('photos') or obj.get('thumbnailUrl')
            urls += walk_images(imgs, source)
        for v in obj.values():
            urls += collect_jsonld_images(v, current_url, source)
    elif isinstance(obj, list):
        for v in obj:
            urls += collect_jsonld_images(v, current_url, source)
    return dedupe_images(urls, source)


def extract_jsonld_images(text: str, current_url: str, source: str = '') -> list[str]:
    urls: list[str] = []
    blocks = re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", text, re.I | re.S)
    for b in blocks:
        s = html.unescape(b).strip()
        try:
            urls += collect_jsonld_images(json.loads(s), current_url, source)
        except Exception:
            continue
    return dedupe_images(urls, source)


def extract_html_images(text: str, source: str = '', current_url: str = '') -> list[str]:
    # Prefer listing-scoped data. A generic full-page scan creates false galleries from
    # recommendations, favicons, maps and agency assets.
    scoped = extract_jsonld_images(text, current_url, source)
    if scoped:
        return scoped

    if source == 'ofim' or source == 'ofim_rss':
        urls = re.findall(r'<div class="item[^>]*>.*?</div>', text, re.I | re.S)
        joined = '\n'.join(x for x in urls if 'carousel' not in x.lower() or True)
        # OFIM current listing carousel uses /Photos/ paths near #thumbs_Carousel.
        idx = text.lower().find('id="thumbs_carousel"')
        segment = text[idx: idx + 50000] if idx >= 0 else text[:70000]
        found = IMG_EXT_RE.findall(segment) + re.findall(r"(?:src|href)=[\"']([^\"']+/Photos/[^\"']+\.(?:jpg|jpeg|png|webp))(?:\?[^\"']*)?[\"']", segment, re.I)
        return dedupe_images([urllib.parse.urljoin(current_url, u) for u in found], source)

    if source == 'immo974':
        # The current property gallery is encoded in data-image attributes.
        found = re.findall(r"data-image=[\"']([^\"']+\.(?:jpg|jpeg|png|webp)(?:\?[^\"']*)?)[\"']", text, re.I)
        if found:
            return dedupe_images([urllib.parse.urljoin(current_url, u) for u in found], source)
        # Fallback to og:image only; don't scan the whole WP page.
        og = re.findall(r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']", text, re.I)
        return dedupe_images([urllib.parse.urljoin(current_url, u) for u in og], source)

    if source == '97immo':
        og = re.findall(r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']", text, re.I)
        return dedupe_images([urllib.parse.urljoin(current_url, u) for u in og], source)

    # For unknown/app-shell pages, return nothing and keep raw/principal image only.
    return []


def dedupe_images(urls: list[str], source: str = '') -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        u = norm_url(u)
        if not likely_listing_image(u, source):
            continue
        # Remove trailing JSON punctuation accidentally captured in query strings.
        u = u.rstrip('.,;')
        # Stable de-dupe by full URL. Keep different real photo filenames.
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def load_raw(path: str | None) -> Any | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(errors='ignore'))
    except Exception:
        return None


def select_record_from_raw(data: Any, row: sqlite3.Row) -> Any:
    """Avoid scanning a whole multipage aggregate as if it belonged to one listing."""
    if isinstance(data, dict) and isinstance(data.get('annonces'), list):
        sid = str(row['source_id'] or '')
        url = str(row['url'] or '').split('?', 1)[0]
        for a in data['annonces']:
            if not isinstance(a, dict):
                continue
            au = str(a.get('url') or '').split('?', 1)[0]
            aid = str(a.get('id') or a.get('source_id') or '')
            if (sid and sid in (aid, au)) or (url and au == url):
                return a
        return {}
    return data


def key(source_site: str | None, source_id: str | None, url: str | None) -> tuple[str, str, str]:
    return (source_site or '', source_id or '', (url or '').split('#', 1)[0])


def cache_path_for(url: str) -> Path:
    h = hashlib.sha256(url.encode('utf-8')).hexdigest()[:20]
    return DETAIL_CACHE / f'{h}.html'


def fetch_detail_html(url: str, source: str) -> dict[str, Any]:
    if not url or not url.startswith(('http://', 'https://')):
        return {'url': url, 'source': source, 'ok': False, 'reason': 'bad_url', 'images': []}
    DETAIL_CACHE.mkdir(parents=True, exist_ok=True)
    p = cache_path_for(url)
    if p.exists() and p.stat().st_size >= MIN_HTML_BYTES:
        text = p.read_text(errors='ignore')
        return {'url': url, 'source': source, 'ok': True, 'cached': True, 'bytes': len(text), 'images': extract_html_images(text, source, url)}
    headers = dict(HEADERS)
    host = urllib.parse.urlparse(url).netloc.lower()
    headers['Referer'] = f'https://{host}/'
    last = ''
    for attempt in range(1, 3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as resp:
                status = getattr(resp, 'status', 200)
                ct = resp.headers.get('Content-Type', '')
                data = resp.read(2_500_000)
                text = data.decode('utf-8', 'ignore')
                if status >= 400:
                    return {'url': url, 'source': source, 'ok': False, 'reason': f'http_{status}', 'content_type': ct, 'images': []}
                if 'text/html' not in ct.lower() and '<html' not in text[:1000].lower():
                    return {'url': url, 'source': source, 'ok': False, 'reason': 'non_html', 'content_type': ct, 'bytes': len(data), 'images': []}
                p.write_text(text)
                return {'url': url, 'source': source, 'ok': True, 'cached': False, 'bytes': len(text), 'images': extract_html_images(text, source, url)}
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
            time.sleep(0.5 * attempt)
    return {'url': url, 'source': source, 'ok': False, 'reason': 'exception', 'error': last, 'images': []}


def patch_embedded(raw: dict[str, Any]) -> None:
    html_path = APP / 'index.html'
    if not html_path.exists():
        return
    text = html_path.read_text()
    marker_start = '<script id="embeddedData" type="application/json">'
    marker_end = '</script>'
    s = text.find(marker_start)
    if s < 0:
        return
    ds = s + len(marker_start)
    e = text.find(marker_end, ds)
    if e < 0:
        return
    compact_payload = dict(raw)
    suspects_path = APP / 'suspects.json'
    if suspects_path.exists() and 'suspects' not in compact_payload:
        try:
            compact_payload['suspects'] = json.loads(suspects_path.read_text()).get('suspects', [])
        except Exception:
            compact_payload['suspects'] = []
    compact = json.dumps(compact_payload, ensure_ascii=False, separators=(',', ':'))
    html_path.write_text(text[:ds] + compact + text[e:])


def main() -> int:
    raw = json.loads(JSON_PATH.read_text())
    items = raw['listings']
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows_by_full = {key(r['source_site'], r['source_id'], r['url']): r for r in con.execute('select * from rental_listings where is_active=1')}
    rows_by_sid = {(r['source_site'] or '', r['source_id'] or ''): r for r in con.execute('select * from rental_listings where is_active=1')}

    # Fetch detail HTML for source pages where raw exports usually contain only the hero image.
    detail_targets = []
    seen_target = set()
    for it in items:
        source = it.get('source_site') or it.get('source') or ''
        if source not in DETAIL_SOURCES:
            continue
        url = it.get('url') or ''
        if not url or url in seen_target:
            continue
        seen_target.add(url)
        detail_targets.append((url, source))

    print(f'GALLERY_DETAIL_START targets={len(detail_targets)} workers={WORKERS}', flush=True)
    detail_by_url: dict[str, dict[str, Any]] = {}
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_detail_html, u, s): u for u, s in detail_targets}
        done = 0
        for fut in cf.as_completed(futs):
            r = fut.result()
            detail_by_url[r['url']] = r
            done += 1
            if done % 50 == 0 or done == len(detail_targets):
                ok = sum(1 for x in detail_by_url.values() if x.get('ok'))
                multi = sum(1 for x in detail_by_url.values() if len(x.get('images') or []) > 1)
                print(f'GALLERY_DETAIL_PROGRESS {done}/{len(detail_targets)} ok={ok} multi_html={multi}', flush=True)

    enriched = 0
    multi = 0
    by_source: dict[str, list[int]] = {}
    examples = []
    exact_row = 0
    sid_row = 0
    detail_used = 0

    for it in items:
        source = it.get('source_site') or it.get('source') or ''
        sid = it.get('source_id') or ''
        r = rows_by_full.get(key(source, sid, it.get('url'))) or rows_by_sid.get((source, sid))
        if r:
            if rows_by_full.get(key(source, sid, it.get('url'))):
                exact_row += 1
            else:
                sid_row += 1
        urls: list[str] = []
        if r:
            data = load_raw(r['raw_json_path'])
            if data is not None:
                data = select_record_from_raw(data, r)
                urls += walk_images(data, source)
        d = detail_by_url.get(it.get('url') or '')
        if d and d.get('images'):
            detail_used += 1
            urls += d['images']
        principal = it.get('image_url')
        ordered = dedupe_images([principal] + urls, source)
        it['image_urls'] = ordered
        it['photo_count'] = len(ordered)
        it['gallery_status'] = 'multi' if len(ordered) > 1 else ('single' if len(ordered) == 1 else 'missing')
        if len(ordered) > 1:
            multi += 1
            if len(examples) < 20:
                examples.append((source, it.get('title'), len(ordered), it.get('url')))
        if ordered:
            enriched += 1
            by_source.setdefault(source or '?', [0, 0, 0])
            by_source[source or '?'][0] += 1
            by_source[source or '?'][1] += len(ordered)
            by_source[source or '?'][2] += 1 if len(ordered) > 1 else 0

    raw.setdefault('meta', {})['gallery_enrichment'] = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'listings_with_image_urls': enriched,
        'multi_photo_listings': multi,
        'detail_targets': len(detail_targets),
        'detail_ok': sum(1 for x in detail_by_url.values() if x.get('ok')),
        'detail_used': detail_used,
        'row_match_exact': exact_row,
        'row_match_by_source_id': sid_row,
    }
    JSON_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    patch_embedded(raw)

    manifest = {
        'generated_at': raw['meta']['gallery_enrichment']['generated_at'],
        'summary': raw['meta']['gallery_enrichment'],
        'details': detail_by_url,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    lines = [
        '# Gallery enrichment V7', '',
        f'- Listings: {len(items)}',
        f'- Listings with image_urls: {enriched}',
        f'- Multi-photo listings: {multi}',
        f'- Detail pages fetched/loaded OK: {raw["meta"]["gallery_enrichment"]["detail_ok"]}/{len(detail_targets)}',
        f'- DB row matches: exact={exact_row}, source_id_fallback={sid_row}',
        f'- Manifest: `{MANIFEST}`',
        '', '## By source',
    ]
    for src, (n, total, src_multi) in sorted(by_source.items()):
        lines.append(f'- {src}: {n} annonces, {total} URLs, avg {total/n:.2f}, multi {src_multi}')
    lines += ['', '## Examples']
    for ex in examples:
        lines.append(f'- {ex[0]} · {ex[2]} photos · {ex[1]} · {ex[3]}')
    OUT.write_text('\n'.join(lines) + '\n')

    print(f'GALLERY_ENRICH_DONE listings={len(items)} multi={multi} enriched={enriched} detail_ok={raw["meta"]["gallery_enrichment"]["detail_ok"]}')
    for ex in examples[:8]:
        print('EXAMPLE', ex)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
