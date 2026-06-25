#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path('/opt/data/projects/reunion-immo-search')
DB = Path(os.environ.get('IMMO_DB_PATH', '/opt/data/data/reunion_watch.db'))
OUTDIR = ROOT / 'artifacts' / 'v3-detail-recovery-20260625'
RAW_ROOT = Path('/opt/data/artifacts/realestate/multi_sources/raw')
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36'
BOILERPLATE_PATTERNS = [
    "L'annonce a bien été ajoutée à vos favoris",
    "Annonce publiée le",
    "Proposée par",
]
TARGET_SOURCES = ['superimmo', 'locamoi', 'domimmo', '97immo', 'citya', 'zimo']


def utcstamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def clean_text(s: str | None) -> str:
    if not s:
        return ''
    s = html.unescape(str(s))
    s = s.replace('\u00a0', ' ').replace('\r', '\n')
    s = re.sub(r'<\s*br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\n\s*\n+', '\n\n', s)
    return s.strip(' \n\t-')


def is_sparse(existing: str | None, title: str | None = None) -> bool:
    e = clean_text(existing)
    t = clean_text(title).lower()
    if not e:
        return True
    if len(e) < 80:
        return True
    if t and e.lower() == t:
        return True
    if "L'annonce a bien été ajoutée à vos favoris" in e:
        return True
    return False


def has_bad_boilerplate(s: str) -> bool:
    low = s.lower()
    return any(p.lower() in low for p in BOILERPLATE_PATTERNS)


def should_update(existing: str | None, new: str | None, title: str | None = None) -> tuple[bool, str]:
    e = clean_text(existing)
    n = clean_text(new)
    if len(n) < 80:
        return False, 'new_too_short'
    if has_bad_boilerplate(n):
        return False, 'new_has_boilerplate'
    # If existing text contains known scraper boilerplate, a shorter cleaned text is
    # still an improvement as long as it is substantial and boilerplate-free.
    if has_bad_boilerplate(e) and len(n) >= 80:
        return True, 'accepted_cleaned_boilerplate'
    if not is_sparse(e, title) and len(n) <= len(e) + 80:
        return False, 'existing_not_sparse_and_new_not_much_longer'
    if len(n) <= len(e):
        return False, 'new_not_longer'
    return True, 'accepted'


def request_text(url: str, *, referer: str | None = None, timeout: int = 25) -> tuple[int, str]:
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7',
        'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
    }
    if referer:
        headers['Referer'] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(1_500_000)
        charset = resp.headers.get_content_charset() or 'utf-8'
        return resp.status, raw.decode(charset, 'replace')


def extract_jsonld(html_text: str) -> list[Any]:
    out = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_text, re.I | re.S):
        body = html.unescape(m.group(1)).strip()
        try:
            data = json.loads(body)
        except Exception:
            continue
        if isinstance(data, list):
            out.extend(data)
        else:
            out.append(data)
    return out


def extract_meta_description(html_text: str) -> str:
    for rx in [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:description["\']',
    ]:
        m = re.search(rx, html_text, re.I | re.S)
        if m:
            return clean_text(m.group(1))
    return ''


def raw_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def superimmo_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    raw = raw_json(row['raw_json_path'])
    text = clean_text(raw.get('text') or '')
    if not text:
        return '', {'method': 'raw_text_missing'}
    # Cut the known card/listing boilerplate and keep the prose after the location token.
    candidates = []
    for pat in [
        r'\([0-9]{5}\)\s+(.+)$',
        r'(?:Appartement|Maison|Villa|Studio)\s*[•-][^\n]{0,120}\)\s+(.+)$',
        r'\b[0-9 ]+\s*€\s*(?:CC|HC)?\s+(?:Appartement|Maison|Villa|Studio)[^A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]{0,10}(.+)$',
    ]:
        m = re.search(pat, text, re.I | re.S)
        if m:
            candidates.append(clean_text(m.group(1)))
    # Fallback: strip up to the second source_id/title-ish location if possible.
    for marker in ['Beau ', 'A louer ', 'À louer ', 'Dans ', 'Situé ', 'Studio ', 'Appartement ']:
        idx = text.find(marker)
        if idx > 80:
            candidates.append(clean_text(text[idx:]))
    candidates = [c for c in candidates if len(c) >= 80 and not has_bad_boilerplate(c)]
    best = max(candidates, key=len, default='')
    return best, {'method': 'superimmo_raw_clean', 'raw_len': len(text)}


def locamoi_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    status, body = request_text(row['url'], referer='https://locamoi.fr/')
    best = ''
    images: list[str] = []
    for data in extract_jsonld(body):
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            typ = item.get('@type')
            if typ == 'RealEstateListing' or 'RealEstate' in str(typ):
                best = clean_text(item.get('description') or best)
                img = item.get('image')
                if isinstance(img, list):
                    images.extend(str(x) for x in img if x)
                elif img:
                    images.append(str(img))
    if not best:
        best = extract_meta_description(body)
    best = re.sub(r'\s*Voir moins\s*$', '', best).strip()
    # Locamoi JSON-LD sometimes duplicates exact paragraph around "Voir moins".
    if len(best) > 200:
        half = len(best) // 2
        if SequenceMatcher(None, best[:half], best[half:]).ratio() > 0.88:
            best = best[:half].strip()
    return best, {'method': 'locamoi_jsonld', 'http_status': status, 'image_count': len(set(images))}


def domimmo_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    def clean_domimmo_desc(s: str) -> str:
        text = clean_text(s)
        # Domimmo/Keldom can append agency/legal syndication footer. Keep source
        # prose above it instead of rejecting the whole detail description.
        footers = [
            'Cette offre de location est proposée',
            'Cette annonce vous est proposée par',
            'Extrait de notre barème',
            'Les informations sur les risques auxquels ce bien est exposé',
            'Géorisques',
        ]
        for marker in footers:
            pos = text.find(marker)
            if pos > 120:
                text = text[:pos].strip()
        return clean_text(text)

    def fetch(params: dict[str, str]) -> tuple[int, list[Any], str]:
        url = 'https://www.keldom.com/api/domimmo/offers?' + urllib.parse.urlencode(params)
        status, body = request_text(url, referer='https://www.domimmo.com/')
        payload = json.loads(body)
        if isinstance(payload, list):
            data = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get('items'), list):
                data = payload['items']
            elif isinstance(payload.get('data'), list):
                data = payload['data']
            else:
                data = [payload]
        else:
            return status, [], 'unsupported_schema'
        return status, data, url

    title = clean_text(row['title'])
    expected_id = str(row['source_id'])
    price = row['rent_eur']
    surf = row['surface_m2']
    attempts: list[dict[str, Any]] = []

    # Exact ID is the most reliable Keldom/Domimmo lookup. Keyword-only search can
    # miss exact listings or rank a wrong generic "Location Appartement 2 pièces".
    query_plan = [
        {'id': expected_id},
        {'motscles': title, 'limit': '8'},
    ]
    # If title search is too generic, add price/city as extra probes. Keldom
    # ignores unknown params harmlessly; scoring below still protects updates.
    if row['city']:
        query_plan.append({'motscles': f"{title} {row['city']}", 'limit': '12'})
    if price is not None:
        query_plan.append({'motscles': str(int(float(price))), 'limit': '12'})

    best = None
    best_score = -1.0
    best_status = None
    total_matches = 0
    for params in query_plan:
        try:
            status, data, used_url = fetch(params)
        except Exception as e:
            attempts.append({'params': params, 'error': repr(e)})
            continue
        total_matches += len(data)
        attempts.append({'params': params, 'http_status': status, 'matches': len(data), 'url': used_url})
        for item in data:
            if not isinstance(item, dict):
                continue
            score = 0.0
            if str(item.get('id')) == expected_id:
                score += 3.0
            score += SequenceMatcher(None, title.lower(), clean_text(item.get('title')).lower()).ratio()
            if price is not None and item.get('price') is not None and abs(float(price) - float(item.get('price'))) <= 5:
                score += 0.5
            if surf is not None and item.get('surface_habitable') is not None and abs(float(surf) - float(item.get('surface_habitable'))) <= 1.0:
                score += 0.5
            if score > best_score:
                best, best_score, best_status = item, score, status
        # Exact id hit is enough; do not keep searching broad queries that may
        # produce a worse false-positive candidate.
        if best and str(best.get('id')) == expected_id:
            break

    if not best:
        return '', {'method': 'domimmo_keldom_api', 'attempts': attempts, 'matches': total_matches, 'best_score': best_score}
    exact = str(best.get('id')) == expected_id
    if not exact and best_score < 1.75:
        return '', {'method': 'domimmo_keldom_api', 'attempts': attempts, 'matches': total_matches, 'best_score': best_score, 'reject': 'low_match'}
    desc = clean_domimmo_desc(best.get('description') or '')
    return desc, {'method': 'domimmo_keldom_api', 'http_status': best_status, 'attempts': attempts, 'matches': total_matches, 'best_score': round(best_score, 3), 'matched_id': best.get('id'), 'photo_count': len(best.get('photos') or [])}


def immo97_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    status, body = request_text(row['url'], referer='https://www.97immo.com/')
    meta = extract_meta_description(body)
    desc = ''
    m = re.search(r'<div[^>]+class=["\'][^"\']*descriptif[^"\']*["\'][^>]*>(.*?)</div>\s*</div>', body, re.I | re.S)
    if m:
        desc = clean_text(m.group(1))
    # grab text around the Descriptif section if class regex misses nested divs
    if len(desc) < 80:
        i = body.lower().find('descriptif')
        if i >= 0:
            desc = clean_text(body[i:i+5000])
    if len(desc) < len(meta):
        desc = meta
    return desc, {'method': '97immo_html_descriptif', 'http_status': status, 'meta_len': len(meta)}


def citya_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    status, body = request_text(row['url'], referer='https://www.citya.com/')
    meta = extract_meta_description(body)
    snippets = []
    for label in ['détails du prix', 'Dépôt de garantie', 'Honoraires charge locataire', 'Libre le']:
        i = body.lower().find(label.lower())
        if i >= 0:
            snippets.append(clean_text(body[max(0, i-200):i+1200]))
    combined = clean_text(meta + '\n' + '\n'.join(snippets))
    return combined, {'method': 'citya_meta_price_details', 'http_status': status, 'meta_len': len(meta)}


def zimo_description(row: sqlite3.Row) -> tuple[str, dict[str, Any]]:
    attempts = []
    source_id = str(row['source_id'])
    # Zimo hides the visible page description behind a rewarded/ad unlock UI, but
    # the Stimulus controller exposes a public JSON endpoint in the server HTML:
    #   /tools/listing/content/<uuid> -> {"content": "full source prose"}
    # Prefer it over the meta description, because the meta text is truncated.
    content_url = f'https://www.zimo.fr/tools/listing/content/{source_id}'
    try:
        status, body = request_text(content_url, referer=row['url'], timeout=20)
        attempts.append({'kind': 'content_endpoint', 'url': content_url, 'status': status, 'len': len(body)})
        data = json.loads(body)
        desc = clean_text(data.get('content') if isinstance(data, dict) else '')
        # Some agency-fed descriptions append generic syndication/footer text that
        # trips the global boilerplate guard. Keep the source prose above it.
        for tail in ["Cette annonce vous est proposée par", "Nos tarifs"]:
            pos = desc.find(tail)
            if pos > 120:
                desc = desc[:pos].strip()
        if len(desc) >= 80:
            return desc, {'method': 'zimo_content_endpoint', 'attempts': attempts}
    except urllib.error.HTTPError as e:
        attempts.append({'kind': 'content_endpoint', 'url': content_url, 'status': e.code, 'error': str(e)})
    except Exception as e:
        attempts.append({'kind': 'content_endpoint', 'url': content_url, 'error': repr(e)})

    # Fallback: public detail HTML meta description. This is better than the card
    # fallback but often truncated with an ellipsis.
    for referer in ['https://www.zimo.fr/', 'https://www.zimo.fr/location/dom-tom/la-reunion']:
        try:
            status, body = request_text(row['url'], referer=referer, timeout=20)
            attempts.append({'kind': 'detail_html', 'referer': referer, 'status': status, 'len': len(body)})
            meta = extract_meta_description(body)
            if len(meta) > 80:
                return meta, {'method': 'zimo_http_meta', 'attempts': attempts}
        except urllib.error.HTTPError as e:
            attempts.append({'kind': 'detail_html', 'referer': referer, 'status': e.code, 'error': str(e)})
        except Exception as e:
            attempts.append({'kind': 'detail_html', 'referer': referer, 'error': repr(e)})
        time.sleep(0.5)
    return '', {'method': 'zimo_content_endpoint_then_meta', 'attempts': attempts, 'blocked': True}


FETCHERS = {
    'superimmo': superimmo_description,
    'locamoi': locamoi_description,
    'domimmo': domimmo_description,
    '97immo': immo97_description,
    'citya': citya_description,
    'zimo': zimo_description,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(DB))
    ap.add_argument('--sources', default=','.join(TARGET_SOURCES))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--sleep', type=float, default=0.8)
    ap.add_argument('--report', default='')
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f'DB not found: {db}')
    stamp = utcstamp()
    report = Path(args.report) if args.report else OUTDIR / f'detail_enrichment_{stamp}.jsonl'
    summary_path = report.with_suffix('.summary.json')
    backup = None
    if not args.dry_run:
        backup = db.with_name(f'{db.name}.bak-detail-v3-{stamp}')
        shutil.copy2(db, backup)

    sources = [s.strip() for s in args.sources.split(',') if s.strip()]
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = []
    for src in sources:
        q = """
        SELECT source_site, source_id, url, title, city, description, raw_json_path, rent_eur, surface_m2
        FROM rental_listings
        WHERE source_site=? AND COALESCE(is_active,1)=1
        ORDER BY length(COALESCE(description,'')) ASC
        """
        for r in con.execute(q, (src,)):
            if src == 'superimmo' or is_sparse(r['description'], r['title']):
                rows.append(r)
    if args.limit:
        rows = rows[:args.limit]

    counts: dict[str, int] = {'attempted': 0, 'accepted': 0, 'updated': 0, 'rejected': 0, 'errors': 0, 'blocked': 0, 'skipped_no_fetcher': 0}
    by_source: dict[str, dict[str, int]] = {}

    with report.open('w', encoding='utf-8') as log:
        for idx, row in enumerate(rows, 1):
            src = row['source_site']
            by_source.setdefault(src, {'attempted': 0, 'accepted': 0, 'updated': 0, 'rejected': 0, 'errors': 0, 'blocked': 0})
            counts['attempted'] += 1
            by_source[src]['attempted'] += 1
            rec: dict[str, Any] = {'idx': idx, 'source': src, 'source_id': row['source_id'], 'url': row['url'], 'old_len': len(clean_text(row['description'])), 'dry_run': args.dry_run}
            fetcher = FETCHERS.get(src)
            if not fetcher:
                counts['skipped_no_fetcher'] += 1
                rec['action'] = 'skip'; rec['reason'] = 'no_fetcher'
                log.write(json.dumps(rec, ensure_ascii=False) + '\n'); log.flush()
                continue
            try:
                new_desc, meta = fetcher(row)
                ok, reason = should_update(row['description'], new_desc, row['title'])
                rec.update({'new_len': len(clean_text(new_desc)), 'reason': reason, 'meta': meta, 'preview': clean_text(new_desc)[:240]})
                if meta.get('blocked'):
                    counts['blocked'] += 1; by_source[src]['blocked'] += 1
                if ok:
                    counts['accepted'] += 1; by_source[src]['accepted'] += 1
                    rec['action'] = 'accept_dry_run' if args.dry_run else 'update_db'
                    if not args.dry_run:
                        con.execute(
                            "UPDATE rental_listings SET description=?, content_hash=? WHERE source_site=? AND source_id=?",
                            (clean_text(new_desc), None, row['source_site'], row['source_id']),
                        )
                        counts['updated'] += 1; by_source[src]['updated'] += 1
                else:
                    counts['rejected'] += 1; by_source[src]['rejected'] += 1
                    rec['action'] = 'reject'
            except Exception as e:
                counts['errors'] += 1; by_source[src]['errors'] += 1
                rec.update({'action': 'error', 'error': repr(e)})
            log.write(json.dumps(rec, ensure_ascii=False) + '\n')
            log.flush()
            if idx < len(rows):
                time.sleep(args.sleep)
    if not args.dry_run:
        con.commit()
    summary = {'generated_at': datetime.now(timezone.utc).isoformat(), 'dry_run': args.dry_run, 'db': str(db), 'backup': str(backup) if backup else None, 'report': str(report), 'counts': counts, 'by_source': by_source}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
