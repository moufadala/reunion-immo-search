#!/usr/bin/env python3
"""Scrape several La Réunion rental sources discovered during reconnaissance.

Sources implemented:
- Immo974 result page (HTML)
- Zimo La Réunion location page (HTML)
- Superimmo La Réunion location page (HTML, basic)
- Locamoi apartment La Réunion page (JSON-LD)

This is technical ingestion, not final alert criteria.
"""
from __future__ import annotations
import argparse, hashlib, html, json, os, re, sqlite3, ssl, subprocess, time
from dataclasses import dataclass, asdict, replace
from html.parser import HTMLParser
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlsplit, urlunsplit, quote, urlencode
from urllib.error import HTTPError

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36 HermesPersonalWatcher/1.0'
RAW=Path(os.environ.get('IMMO_RAW_DIR', '/opt/data/artifacts/realestate/multi_sources/raw'))
APIFY_USAGE_REPORT = os.environ.get('IMMO_APIFY_USAGE_REPORT')
if not APIFY_USAGE_REPORT and os.environ.get('IMMO_REFRESH_RUN_DIR'):
    APIFY_USAGE_REPORT = str(Path(os.environ['IMMO_REFRESH_RUN_DIR']) / 'apify_usage.jsonl')
CTX=ssl.create_default_context()

# --- Scrapling pilot wiring (optional, with graceful fallback) -------------
# Importing must never break the scraper: if the helper is missing we run the
# historical urllib path exactly as before.
try:
    import scrapling_fetch as _sf  # sibling module in scripts/
except Exception:  # pragma: no cover
    _sf = None
SCRAPLING_MODE = 'auto'      # 'auto' | 'scrapling' | 'off'  (set from CLI in main)
SCRAPLING_ENGINE = 'http'    # 'http' | 'stealth' | 'auto'
FETCH_LOG = []               # per-fetch instrumentation (mode/status/duration/error_class)
SOURCE_RUNTIME_META = {}     # adapter-only evidence (dataset ids, raw counts, caps)

# Safety caps are adapter arguments now. Reaching one is recorded in that
# adapter's runtime metadata; a fixed global cap would wrongly downgrade a
# genuinely exhausted small catalogue.
BOUNDED_PARTIAL_SOURCES = set()
SOURCE_RESULT_CAPS = {}

@dataclass
class Listing:
    source_site: str
    source_id: str
    url: str
    canonical_url: str|None
    title: str|None
    city: str|None
    district: str|None
    property_type: str|None
    rooms: int|None
    bedrooms: int|None
    surface_m2: float|None
    rent_eur: int|None
    charges_eur: int|None
    agency_or_owner: str|None
    published_at: str|None
    image_url: str|None
    description: str|None
    raw_json_path: str|None
    content_hash: str

def clean(s):
    if s is None: return None
    s=html.unescape(str(s))
    s=re.sub(r'<[^>]+>',' ',s)
    s=re.sub(r'\s+',' ',s).strip()
    return s or None

def to_int_price(s):
    if s is None: return None
    m=re.search(r'([0-9][0-9\s\u202f\xa0.,]*)',str(s))
    if not m: return None
    x=m.group(1).replace('\u202f',' ').replace('\xa0',' ').replace(' ','').replace(',','.')
    try: return int(round(float(x)))
    except: return None

def parse_surface(s):
    if not s: return None
    m=re.search(r'([0-9]+(?:[,.][0-9]+)?)\s*m(?:²|2)',str(s),re.I)
    if not m: return None
    return float(m.group(1).replace(',','.'))

def parse_rooms(s):
    if not s: return None
    m=re.search(r'(?:T|F)?\s*([1-9])\s*(?:pi[eè]ces?|p\b)',str(s),re.I)
    return int(m.group(1)) if m else None

def parse_rent_eur(s):
    if not s: return None
    txt=str(s).replace('\u202f',' ')
    candidates=[]
    # Prefer amounts explicitly tied to rent/loyer.
    for m in re.finditer(r'(?:loyer|prix)[^0-9€]{0,40}([0-9][0-9\s.,]{1,8})\s*(?:€|eur)?', txt, re.I):
        candidates.append(m.group(1))
    # Then any amount followed by euro sign; avoids taking rooms/surface from titles.
    for m in re.finditer(r'([0-9][0-9\s.,]{1,8})\s*(?:€|eur)', txt, re.I):
        candidates.append(m.group(1))
    for raw in candidates:
        val=to_int_price(raw)
        if val and 250 <= val <= 6000:
            return val
    return None

def _legacy_fetch(url, method='GET', data=None):
    # urllib cannot send raw Unicode IRIs in the request line; encode path/query.
    parts=urlsplit(url)
    url=urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe='/%'), quote(parts.query, safe='=&?/%'), parts.fragment))
    headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml,application/json,text/plain,*/*','Accept-Language':'fr-FR,fr;q=0.9','Referer':'https://www.google.com/'}
    body=None
    if data is not None:
        from urllib.parse import urlencode
        body=urlencode(data).encode(); headers['Content-Type']='application/x-www-form-urlencoded'
    req=Request(url,data=body,headers=headers,method=method)
    with urlopen(req,timeout=40,context=CTX) as r:
        return r.read().decode('utf-8','replace'), r.url


def _tls_eof_error(exc):
    """Recognize only the urllib/OpenSSL premature-EOF failure family."""
    pending = [exc]
    seen = set()
    ssl_error = False
    text = []
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        ssl_error = ssl_error or isinstance(current, ssl.SSLError)
        text.append(str(current).lower())
        pending.extend((getattr(current, 'reason', None), getattr(current, '__cause__', None)))
    joined = ' '.join(text)
    return ssl_error and (
        'unexpected_eof' in joined
        or 'eof occurred in violation of protocol' in joined
    )


def _immo974_curl_fetch(url, method='GET', data=None):
    parts = urlsplit(url)
    if (
        parts.scheme.lower() != 'https'
        or (parts.hostname or '').lower() != 'www.immo974.com'
        or method.upper() != 'GET'
        or data is not None
    ):
        raise RuntimeError('curl TLS fallback refused outside bounded Immo974 GET')
    encoded = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
    argv = [
        'curl', '--fail', '--silent', '--show-error', '--location',
        '--max-time', '40', '--user-agent', UA,
        '--header', 'Accept: text/html,application/xhtml+xml,application/json,text/plain,*/*',
        '--header', 'Accept-Language: fr-FR,fr;q=0.9',
        '--header', 'Referer: https://www.google.com/', encoded,
    ]
    completed = subprocess.run(argv, capture_output=True, timeout=45, check=False)
    if completed.returncode != 0 or not completed.stdout:
        detail = completed.stderr.decode('utf-8', 'replace')[-500:]
        raise RuntimeError(f'Immo974 curl fallback failed rc={completed.returncode}: {detail}')
    return completed.stdout.decode('utf-8', 'replace'), url


def fetch(url, method='GET', data=None):
    """Fetch HTML, preferring Scrapling when enabled, else the historical urllib
    path. Records one instrumentation row per call in FETCH_LOG. On failure it
    raises (as the legacy code did) so the caller marks the source failed."""
    if _sf is None or SCRAPLING_MODE == 'off':
        # Strict historical behaviour, plus a lightweight instrumentation row.
        t0=__import__('time').time()
        try:
            text, final=_legacy_fetch(url, method=method, data=data)
            FETCH_LOG.append({'url':url,'mode':'fallback','engine':'fallback','ok':bool(text),
                              'status':None,'duration_ms':int((__import__('time').time()-t0)*1000),
                              'error':None,'error_class':'none','reason':None,'text_len':len(text or '')})
            return text, final
        except Exception as e:
            status=getattr(e,'code',None)
            ec, reason=(('site-antibot',f'http_{status}') if status in (401,403,429,503)
                        else ('network',type(e).__name__))
            if _tls_eof_error(e) and (urlsplit(url).hostname or '').lower() == 'www.immo974.com' and method.upper() == 'GET' and data is None:
                try:
                    text, final = _immo974_curl_fetch(url, method=method, data=data)
                except Exception as curl_error:
                    FETCH_LOG.append({'url':url,'mode':'curl_tls_eof_fallback','engine':'curl','ok':False,
                              'status':None,
                              'duration_ms':int((__import__('time').time()-t0)*1000),
                              'error':f'{type(curl_error).__name__}: {curl_error}',
                              'error_class':'network','reason':'urllib_tls_eof_curl_failed','text_len':0})
                    raise e
                FETCH_LOG.append({'url':url,'mode':'curl_tls_eof_fallback','engine':'curl','ok':True,
                                  'status':None,'duration_ms':int((__import__('time').time()-t0)*1000),
                                  'error':None,'error_class':'none','reason':'urllib_tls_eof','text_len':len(text)})
                return text, final
            FETCH_LOG.append({'url':url,'mode':'fallback','engine':'fallback','ok':False,
                              'status':status if isinstance(status,int) else None,
                              'duration_ms':int((__import__('time').time()-t0)*1000),
                              'error':f'{type(e).__name__}: {e}','error_class':ec,'reason':reason,'text_len':0})
            raise
    outcome=_sf.fetch_html(
        url, fallback=lambda u:_legacy_fetch(u, method=method, data=data),
        mode=SCRAPLING_MODE, engine=SCRAPLING_ENGINE, method=method, data=data,
    )
    FETCH_LOG.append(outcome.summary())
    if not outcome.ok:
        raise RuntimeError(outcome.error or f'fetch failed [{outcome.error_class}] {url}')
    return outcome.text, (outcome.final_url or url)

def hash_listing(d):
    return hashlib.sha256(json.dumps(d,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def save_raw(site,sid,obj):
    RAW.mkdir(parents=True,exist_ok=True)
    path=RAW/f'{site}_{re.sub(r"[^a-zA-Z0-9_-]+","_",sid)[:120]}.json'
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    return str(path)

TARGET_SCOPE = ('Saint-Denis', 'Sainte-Marie')
ZIMO_CITY_ROUTES = {
    'Saint-Denis': 'https://www.zimo.fr/annonces/immobilier/location/saint-denis-97400',
    'Sainte-Marie': 'https://www.zimo.fr/annonces/immobilier/location/sainte-marie-97438',
}
SUPERIMMO_CITY_ROUTES = {
    'Saint-Denis': 'https://www.superimmo.com/location/dom-tom/la-reunion/saint-denis-974',
    'Sainte-Marie': 'https://www.superimmo.com/location/dom-tom/la-reunion/sainte-marie-97438',
}
FNAIM_CITY_ROUTES = {
    'Saint-Denis': 'https://www.fnaim.re/38244-st-denis/locations',
    'Sainte-Marie': 'https://www.fnaim.re/38245-ste-marie/locations/appartements',
}


def _reported_total(text):
    patterns = [
        r'<meta[^>]+name=["\']total-results["\'][^>]+content=["\']([0-9\s]+)',
        r'<meta[^>]+content=["\']([0-9\s]+)["\'][^>]+name=["\']total-results["\']',
        r'\bdata-total(?:-results)?=["\']([0-9\s]+)',
        r'["\']total(?:Results|_results|Count)?["\']\s*:\s*([0-9]+)',
        r'\b([0-9][0-9\s]*)\s+(?:annonces?|biens?)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text or '', re.I)
        if match:
            return int(re.sub(r'\s+', '', match.group(1)))
    return None


def _has_next_page(text, next_page):
    source = html.unescape(text or '')
    if re.search(r'<(?:a|link)\b[^>]*\brel=["\'][^"\']*\bnext\b', source, re.I):
        return True
    if re.search(r'<a\b[^>]*\bhref=["\'][^"\']*(?:[?&]page=|/p/)' + str(next_page) + r'(?:\D|$)', source, re.I):
        return True
    return False


def _blocked_or_challenge_page(text):
    low = (text or '').lower()
    return any(marker in low for marker in (
        'attention required! | cloudflare',
        'sorry, you have been blocked',
        'cf-chl-',
        'access denied',
        'enable cookies to continue',
    ))


def _target_city(value):
    value = clean(value)
    if not value:
        return None
    low = value.lower().replace('-', ' ').replace('é', 'e').replace('è', 'e')
    low = re.sub(r'\s+', ' ', low)
    if re.search(r'\b(?:saint|st)\s+denis\b', low) or re.search(r'\b(?:sainte|ste)\s+clotilde\b', low):
        return 'Saint-Denis'
    if re.search(r'\b(?:sainte|ste)\s+marie\b', low):
        return 'Sainte-Marie'
    return None


def _set_snapshot_meta(source, *, found, route_states, pages_attempted,
                       pages_succeeded, raw_items, signals, extra=None):
    signals = list(dict.fromkeys(str(item) for item in signals if item))
    full = (
        bool(route_states)
        and all(row.get('status') == 'complete' for row in route_states.values())
        and pages_attempted == pages_succeeded
        and not signals
    )
    meta = {
        'pages_attempted': pages_attempted,
        'pages_succeeded': pages_succeeded,
        'raw_items': raw_items,
        'parsed_items': len(found),
        'unique_ids': len({str(item[1]) for item in found}),
        'unparsed_items_by_reason': {},
        'pre_unique_rejections_by_reason': {},
        'rejected_items_by_reason': {},
        'route_states': route_states,
        'truncation_signals': signals,
        'full_snapshot_proof': full,
        'snapshot_proof': 'all_target_routes_exhausted' if full else None,
    }
    if extra:
        meta.update(extra)
    SOURCE_RUNTIME_META[source] = meta
    return meta


def _add_runtime_signal(source, signal):
    meta = SOURCE_RUNTIME_META[source]
    signals = list(meta.get('truncation_signals') or [])
    if signal not in signals:
        signals.append(signal)
    meta['truncation_signals'] = signals
    meta['full_snapshot_proof'] = False
    meta['snapshot_proof'] = None


def _merge_rejection_reasons(source, reasons):
    meta = SOURCE_RUNTIME_META[source]
    merged = dict(meta.get('rejected_items_by_reason') or {})
    for reason, count in reasons.items():
        if count:
            merged[reason] = int(merged.get(reason, 0)) + int(count)
    meta['rejected_items_by_reason'] = merged


def _walk_target_routes(*, source, routes, max_pages, max_items, delay,
                        page_url, parse_items, item_key, fetch_retries=0):
    found = []
    all_unique_global = set()
    safety_rejected = set()
    route_states = {}
    signals = []
    pages_attempted = 0
    pages_succeeded = 0
    raw_items = 0
    retries = 0
    pre_unique_rejections = {'missing_id': 0, 'duplicate_raw': 0}
    cap_reached = False

    for city, base in routes.items():
        route_ids = set()
        state = {'status': 'partial', 'terminal': None, 'items': 0}
        if cap_reached:
            state['terminal'] = f'safety_item_cap_reached:{max_items}'
            route_states[city] = state
            continue
        for page in range(1, max_pages + 1):
            url = page_url(base, page)
            pages_attempted += 1
            fetch_succeeded = False
            for attempt in range(fetch_retries + 1):
                try:
                    text, _ = fetch(url)
                    fetch_succeeded = True
                    break
                except Exception:
                    if attempt < fetch_retries:
                        retries += 1
                        time.sleep(delay * (attempt + 1))
                        continue
                    signals.append('fetch_failure')
                    state['terminal'] = f'fetch_failure:page:{page}'
            if not fetch_succeeded:
                break
            pages_succeeded += 1
            page_items = list(parse_items(text))
            raw_items += len(page_items)
            if _blocked_or_challenge_page(text):
                signals.append('blocked_or_challenge_page')
                state['terminal'] = f'blocked_or_challenge_page:page:{page}'
                break
            new_route = []
            for item in page_items:
                key = str(item_key(item) or '')
                if not key:
                    pre_unique_rejections['missing_id'] += 1
                    continue
                if key in route_ids:
                    pre_unique_rejections['duplicate_raw'] += 1
                    continue
                route_ids.add(key)
                new_route.append((key, item))
                if key not in all_unique_global:
                    all_unique_global.add(key)
                    if len(found) < max_items:
                        found.append((city, key, item))
                    else:
                        safety_rejected.add(key)
                else:
                    pre_unique_rejections['duplicate_raw'] += 1
            state['items'] = len(route_ids)
            total = _reported_total(text)
            if total is not None:
                state['reported_total'] = total
            if total is not None and len(route_ids) >= total:
                state.update(status='complete', terminal='reported_total')
                break
            if not page_items:
                state.update(status='complete', terminal='empty_page')
                break
            if not _has_next_page(text, page + 1):
                state.update(status='complete', terminal='no_next')
                break
            if not new_route:
                signals.append('pagination_stalled')
                state['terminal'] = f'pagination_stalled:page:{page}'
                break
            if safety_rejected or len(found) >= max_items:
                signals.append(f'safety_item_cap_reached:{max_items}')
                state['terminal'] = f'safety_item_cap_reached:{max_items}'
                cap_reached = True
                break
            if page == max_pages:
                signals.append(f'safety_page_cap_reached:{max_pages}')
                state['terminal'] = f'safety_page_cap_reached:{max_pages}'
                break
            time.sleep(delay)
        route_states[city] = state

    _set_snapshot_meta(
        source, found=[(None, key, None) for key in all_unique_global],
        route_states=route_states,
        pages_attempted=pages_attempted, pages_succeeded=pages_succeeded,
        raw_items=raw_items, signals=signals,
        extra={
            'parsed_items': raw_items - pre_unique_rejections['missing_id'],
            'unique_ids': len(all_unique_global),
            'unparsed_items_by_reason': (
                {'missing_id': pre_unique_rejections['missing_id']}
                if pre_unique_rejections['missing_id'] else {}
            ),
            'retries': retries,
            'pre_unique_rejections_by_reason': (
                {'duplicate_raw': pre_unique_rejections['duplicate_raw']}
                if pre_unique_rejections['duplicate_raw'] else {}
            ),
            'rejected_items_by_reason': (
                {'safety_item_cap': len(safety_rejected)} if safety_rejected else {}
            ),
        },
    )
    return found
def _zimo_articles(text):
    return re.findall(r'<article\b[^>]*>(.*?)</article>', text or '', re.I | re.S)


def _zimo_article_id(article):
    match = re.search(r'<a href=["\'](/annonce/[^"\']+)', article, re.I | re.S)
    return match.group(1).rstrip('/').split('/')[-1] if match else None


def scrape_zimo(max_pages=50, max_items=5000, delay=2.0):
    found = _walk_target_routes(
        source='zimo', routes=ZIMO_CITY_ROUTES, max_pages=max_pages,
        max_items=max_items, delay=delay,
        page_url=lambda base, page: base if page == 1 else f'{base}?page={page}',
        parse_items=_zimo_articles, item_key=_zimo_article_id,
    )
    out = []
    rejected_out_of_scope = set()
    rejected_commercial = set()
    rejected_mapping = set()
    for route_city, sid, article in found:
        match = re.search(r'<a href=["\'](/annonce/[^"\']+)["\'][^>]*title=["\']([^"\']+)', article, re.I | re.S)
        if not match:
            rejected_mapping.add(sid)
            continue
        url = 'https://www.zimo.fr' + html.unescape(match.group(1))
        price = re.search(r'<span class=["\']badge ink base["\']>\s*([^<]*€)', article, re.I | re.S)
        info = re.search(r'<div class=["\'][^"\']*font-medium[^"\']*["\']>\s*(.*?)\s*</div>', article, re.I | re.S)
        source = re.search(r'<span>([^<]+)</span>\s*<i class=["\']fas fa-caret-right', article, re.I | re.S)
        tim = re.search(r'<time>(.*?)</time>', article, re.I | re.S)
        title = clean(match.group(2))
        inf = clean(info.group(1)) if info else title
        city_match = re.search(r'Location\s+(?:T\d\s+)?([^()]+?)\s*\(974\)', inf or '', re.I)
        observed_city = clean(city_match.group(1)) if city_match else None
        observed_target = _target_city(observed_city)
        if observed_city and observed_target != route_city:
            rejected_out_of_scope.add(sid)
            continue
        city = observed_target or route_city
        data = {
            'url': url, 'title': title, 'info': inf,
            'price': clean(price.group(1)) if price else None,
            'source': clean(source.group(1)) if source else None,
            'time': clean(tim.group(1)) if tim else None,
            'image': extract_image_url(article, 'https://www.zimo.fr/'),
        }
        low = (inf or '').lower()
        ptype = 'commercial' if any(x in low for x in ['bureau', 'bureaux', 'local commercial', 'commerce']) else ('box' if any(x in low for x in ['box', 'garde meuble']) else ('house' if 'maison' in low else ('flat' if any(x in low for x in ['appartement', 'studio', 'duplex', 't1', 't2', 't3', 't4']) else None)))
        if ptype in ('commercial', 'box'):
            rejected_commercial.add(sid)
            continue
        out.append(Listing('zimo', sid, url, url, title, city, None, ptype,
                           parse_rooms(inf), None, parse_surface(inf),
                           to_int_price(data['price']), None, data['source'],
                           data['time'], data['image'], inf,
                           save_raw('zimo', sid, data), hash_listing(data)))
    rejection_reasons = {
        reason: len(ids) for reason, ids in {
            'out_of_scope': rejected_out_of_scope,
            'commercial': rejected_commercial,
            'mapping_missing_fields': rejected_mapping,
        }.items() if ids
    }
    SOURCE_RUNTIME_META['zimo'].update(
        rejected_out_of_scope=len(rejected_out_of_scope),
    )
    _merge_rejection_reasons('zimo', rejection_reasons)
    return out


def _superimmo_articles(text):
    return re.findall(
        r'<article\b[^>]*data-public-id=["\']([^"\']+)["\'][^>]*>(.*?)</article>',
        text or '', re.I | re.S,
    )


def scrape_superimmo(max_pages=50, max_items=2000, delay=5):
    found = _walk_target_routes(
        source='superimmo', routes=SUPERIMMO_CITY_ROUTES,
        max_pages=max_pages, max_items=max_items, delay=delay,
        page_url=lambda base, page: base if page == 1 else f'{base}/p/{page}',
        parse_items=_superimmo_articles, item_key=lambda item: item[0],
        fetch_retries=2,
    )
    out = []
    rejected_out_of_scope = set()
    rejected_commercial = set()
    for route_city, sid, (_, article) in found:
        url_match = re.search(r'data-url-with-next-prev=["\']([^"\']+)', article, re.I) or re.search(r'data-js-url=["\']([^"\']*/annonces/[^"\']+)', article, re.I)
        url = urljoin('https://www.superimmo.com', html.unescape(url_match.group(1))) if url_match else f'https://www.superimmo.com/annonces/{sid}'
        observed_city = city_from_url(url)
        observed_target = _target_city(observed_city)
        if observed_city and observed_target != route_city:
            rejected_out_of_scope.add(sid)
            continue
        txt = clean(article) or ''
        if any(token in url.lower() for token in ('bureau', 'commerce', 'local-commercial', 'parking', 'terrain')):
            rejected_commercial.add(sid)
            continue
        price_match = re.search(r'([0-9]{2,4})\s*€\s*CC', txt)
        price = to_int_price(price_match.group(1)) if price_match else None
        title = clean(re.sub(r'-x[0-9a-z]+.*', '', url.split('/annonces/')[-1]).replace('-', ' ')) if '/annonces/' in url else txt[:120]
        data = {'url': url, 'title': title, 'text': txt[:500], 'price': price,
                'image': extract_image_url(article, 'https://www.superimmo.com/')}
        out.append(Listing('superimmo', sid, url, url, title,
                           observed_target or route_city, None,
                           'flat' if 'appartement' in url else ('house' if 'maison' in url else None),
                           parse_rooms(txt), None, parse_surface(txt), price, None,
                           None, None, data['image'], txt[:500],
                           save_raw('superimmo', sid, data), hash_listing(data)))
    rejection_reasons = {
        reason: len(ids) for reason, ids in {
            'out_of_scope': rejected_out_of_scope,
            'commercial': rejected_commercial,
        }.items() if ids
    }
    SOURCE_RUNTIME_META['superimmo'].update(
        rejected_out_of_scope=len(rejected_out_of_scope),
    )
    _merge_rejection_reasons('superimmo', rejection_reasons)
    return out

def meta_content(text, name):
    esc=re.escape(name)
    m=re.search(r'<meta[^>]+(?:property|name)=(["\'])'+esc+r'\1[^>]+content=(["\'])(.*?)\2',text,re.I)
    if m:
        return clean(m.group(3))
    m=re.search(r'<meta[^>]+content=(["\'])(.*?)\1[^>]+(?:property|name)=(["\'])'+esc+r'\3',text,re.I)
    if m:
        return clean(m.group(2))
    return None

def title_tag(text):
    m=re.search(r'<title[^>]*>(.*?)</title>',text,re.I|re.S)
    return clean(m.group(1)) if m else None

def first_srcset_url(srcset):
    if not srcset: return None
    vals=[]
    for part in str(srcset).split(','):
        u=part.strip().split()[0] if part.strip() else None
        if u: vals.append(u)
    return vals[0] if vals else None

def normalize_image_url(u, base):
    if not u: return None
    u=html.unescape(str(u)).strip().strip('"\'')
    if not u or u.startswith('data:') or u.startswith('blob:'):
        return None
    low=u.lower()
    # Reject non-listing visuals: logos, UI icons, social/share assets, DPE badges,
    # and SVGs. A coverage gate must mean a real property photo, not merely any image.
    if any(x in low for x in [
        'logo', 'sprite', 'placeholder', 'blank.', 'blank-', 'default-avatar',
        'favicon', 'nophoto', '/pro/photos/', 'jumbotron', 'no_bien',
        '/images/agences/', 'getdpe/', 'facebook.svg', 'twitter.svg',
        'linkedin.svg', 'share', 'chevron', '/icons/', '/icon-', 'picto',
    ]):
        return None
    if re.search(r'\.(svg|ico)(\?|#|$)', low):
        return None
    return urljoin(base, u)

def extract_jsonld_images(text, base):
    out=[]
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text or '', re.I|re.S):
        raw=html.unescape(m.group(1)).strip()
        try:
            obj=json.loads(raw)
        except Exception:
            continue
        stack=[obj]
        while stack:
            x=stack.pop()
            if isinstance(x, dict):
                for k,v in x.items():
                    if k.lower() in ('image','photo','photos','thumbnailurl','contenturl'):
                        if isinstance(v, str):
                            u=normalize_image_url(v, base)
                            if u: out.append(u)
                        elif isinstance(v, list):
                            stack.extend(v)
                        elif isinstance(v, dict):
                            stack.append(v)
                    else:
                        if isinstance(v, (dict,list)): stack.append(v)
            elif isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, str):
                u=normalize_image_url(x, base)
                if u and re.search(r'\.(jpe?g|png|webp)(\?|$)', u, re.I): out.append(u)
    return out

def extract_image_url(text, base):
    # Prefer semantic metadata from detail pages.
    for name in ['og:image:secure_url','og:image','twitter:image','twitter:image:src']:
        u=normalize_image_url(meta_content(text, name), base)
        if u: return u
    imgs=extract_jsonld_images(text, base)
    if imgs: return imgs[0]
    # Common lazy/image attributes, including srcset.
    attr_patterns=[
        r'<(?:img|source)[^>]+(?:srcset|data-srcset)=["\']([^"\']+)["\']',
        r'<img[^>]+(?:data-src|data-lazy-src|data-original|data-url|src)=["\']([^"\']+)["\']',
        r'background-image\s*:\s*url\(["\']?([^"\')]+)["\']?\)',
        r'https?:\\?/\\?/[^"\'<>\\]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'<>\\]*)?'
    ]
    for pat in attr_patterns:
        for m in re.finditer(pat, text or '', re.I|re.S):
            raw=m.group(1) if m.groups() else m.group(0)
            if 'srcset' in pat or ',' in raw:
                raw=first_srcset_url(raw) or raw
            raw=raw.replace('\\/', '/')
            u=normalize_image_url(raw, base)
            if u: return u
    return None

def guess_city_from_text(s):
    if not s: return None
    aliases=[
        ('Saint-Denis',['saint denis','st denis']), ('Sainte-Clotilde',['sainte clotilde','ste clotilde','st clotilde']),
        ('Sainte-Marie',['sainte marie','ste marie']), ('Saint-Pierre',['saint pierre','st pierre']),
        ('Saint-Paul',['saint paul','st paul']), ('Saint-Louis',['saint louis','st louis']),
        ('Le Tampon',['le tampon','tampon']), ('Le Port',['le port']), ('La Possession',['la possession']),
        ('Saint-Leu',['saint leu','st leu']), ('Saint-André',['saint andre','saint andré','st andre','st andré']),
        ('Bras-Panon',['bras panon']), ('Etang-Salé',['etang sale','étang salé','letang sale','l etang sale']),
        ('La Saline',['la saline','saline les bains']), ('Moufia',['moufia']), ('Bellepierre',['bellepierre'])]
    low=s.lower().replace('-', ' ').replace("'", ' ')
    for canon, vals in aliases:
        if any(v in low for v in vals):
            return canon
    m=re.search(r'\b(974\d{2})\b',s)
    return m.group(1) if m else None

def city_from_url(url):
    part=urlsplit(url).path.replace('-', ' ')
    return guess_city_from_text(part)

def parse_97immo_precise(html_text):
    """97immo publie prix et chambres dans des blocs propres et bien
    identifies -- plus fiable que le texte aplati de la page entiere, qui
    colle le compteur de photos ("3" de "3 photos") juste devant le prix.
    Verifie le 2026-07-27 sur l'annonce 943 : etiquette_nbphoto=3,
    etiquette_prix=615, chambres=1 (tags-bien) -- confirme contre le vrai site.
    """
    out = {}
    m = re.search(r'class="etiquette_prix"[^>]*>([^<]+?)&euro;', html_text)
    if m:
        prix = to_int_price(m.group(1))
        if prix and 250 <= prix <= 6000:
            out['rent'] = prix
    m = re.search(r'<li><h3>(\d+)\s*Chambres?</h3></li>', html_text, re.I)
    if m:
        out['bedrooms'] = int(m.group(1))
    m = re.search(r'<li><h3>(\d+)\s*Pi.ces?</h3></li>', html_text, re.I)
    if m:
        out['rooms'] = int(m.group(1))
    return out


def detail_listing(source, url, ptype=None, sid=None):
    text, final = fetch(url)
    title = meta_content(text,'og:title') or title_tag(text)
    desc = meta_content(text,'description') or meta_content(text,'og:description') or clean(text)[:900]
    joined = ' '.join(x for x in [title, desc, clean(text)] if x)
    sid = sid or hashlib.md5(final.encode()).hexdigest()[:16]
    d={'url':final,'title':title,'description':desc,'image': extract_image_url(text, final)}
    # Prefer structured/title/meta fields before whole page to avoid related-listing prices.
    title_desc = ' '.join(x for x in [title, desc] if x)
    rent = parse_rent_eur(title) or parse_rent_eur(desc) or parse_rent_eur(joined)
    surface = parse_surface(title_desc) or parse_surface(joined)
    rooms = parse_rooms(title_desc) or parse_rooms(joined)
    city = city_from_url(final) or guess_city_from_text(title_desc) or guess_city_from_text(joined)
    bedrooms = None
    if source == '97immo':
        precis = parse_97immo_precise(text)
        rent = precis.get('rent', rent)
        rooms = precis.get('rooms', rooms)
        bedrooms = precis.get('bedrooms')
    return Listing(source, sid, final, final, title, city, None, ptype,
                   rooms, bedrooms, surface, rent, None,
                   None, None, d['image'], desc, save_raw(source,sid,d), hash_listing(d))

def unique_links(text, pattern, base, limit=25):
    seen=[]
    for u in re.findall(pattern,text,re.I|re.S):
        u=html.unescape(u)
        full=urljoin(base,u)
        if full not in seen:
            seen.append(full)
        if len(seen)>=limit: break
    return seen

def _fnaim_links(text):
    return unique_links(
        text, r'href=["\']([^"\']*id-location-[^"\']+)["\']',
        'https://www.fnaim.re/', 5000,
    )


def scrape_fnaim(max_items=2000, max_pages=50, delay=1.5):
    found = _walk_target_routes(
        source='fnaim', routes=FNAIM_CITY_ROUTES, max_pages=max_pages,
        max_items=max_items, delay=delay,
        page_url=lambda base, page: f'{base}/{page}',
        parse_items=_fnaim_links, item_key=lambda url: url,
        fetch_retries=2,
    )
    out = []
    detail_failures = set()
    rejected_out_of_scope = set()
    rejected_commercial = set()
    for route_city, _, url in found:
        if any(token in url.lower() for token in ('bureau', 'commerce', 'local-', 'parking', 'terrain')):
            rejected_commercial.add(url)
            continue
        sid_match = re.search(r'id-location-[^/]+-(\d+)/?', url)
        sid = sid_match.group(1) if sid_match else None
        listing = None
        for attempt in range(3):
            try:
                listing = detail_listing(
                    'fnaim', url, 'house' if 'maison' in url else 'flat', sid,
                )
                break
            except Exception:
                if attempt < 2:
                    SOURCE_RUNTIME_META['fnaim']['retries'] = int(
                        SOURCE_RUNTIME_META['fnaim'].get('retries', 0) or 0
                    ) + 1
                    time.sleep(delay * (attempt + 1))
        if listing is None:
            detail_failures.add(url)
            continue
        observed = listing.city
        observed_target = _target_city(observed)
        if observed and observed_target != route_city:
            rejected_out_of_scope.add(url)
            continue
        out.append(replace(listing, city=observed_target or route_city))
        time.sleep(delay)
    rejection_reasons = {
        reason: len(ids) for reason, ids in {
            'detail_fetch_failure': detail_failures,
            'out_of_scope': rejected_out_of_scope,
            'commercial': rejected_commercial,
        }.items() if ids
    }
    SOURCE_RUNTIME_META['fnaim'].update(
        detail_fetch_failures=len(detail_failures),
        rejected_out_of_scope=len(rejected_out_of_scope),
    )
    _merge_rejection_reasons('fnaim', rejection_reasons)
    if detail_failures:
        _add_runtime_signal('fnaim', 'detail_fetch_failure')
    return out

OFIM_CATEGORIES = {'appartements': 'flat', 'villas': 'house'}


def scrape_ofim_rss(max_items=50):
    text,_=fetch('https://www.ofim.fr/rss.xml')
    items=re.findall(r'<item\b[^>]*>(.*?)</item>',text,re.I|re.S)[:max_items]
    out=[]
    for it in items:
        mt=re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>',it,re.I|re.S) or re.search(r'<title>(.*?)</title>',it,re.I|re.S)
        title=clean(mt.group(1)) if mt else None
        link_m=re.search(r'<link>\s*([^<]+)',it,re.I)
        if not link_m or not title or 'location' not in title.lower(): continue
        url=clean(link_m.group(1))
        sid_m=re.search(r'/([0-9]+)/',url or '') or re.search(r'/([0-9]+)/',it)
        sid=sid_m.group(1) if sid_m else hashlib.md5((url or title).encode()).hexdigest()[:16]
        md=re.search(r'<description><!\[CDATA\[(.*?)\]\]></description>',it,re.I|re.S) or re.search(r'<description>(.*?)</description>',it,re.I|re.S)
        desc=clean(md.group(1)) if md else None
        joined=' '.join([title or '',desc or ''])
        d={'url':url,'title':title,'description':desc}
        image=None
        try:
            detail_text, detail_final = fetch(url)
            image=extract_image_url(detail_text, detail_final)
            d['image']=image
        except Exception as e:
            d['image_error']=repr(e)
        out.append(Listing('ofim_rss',sid,url,url,title,guess_city_from_text(joined),None,'house' if 'maison' in joined.lower() else 'flat',parse_rooms(joined),None,parse_surface(joined),parse_rent_eur(joined),None,'OFIM',None,image,desc,save_raw('ofim_rss',sid,d),hash_listing(d)))
    return out

CITYA_COMMUNES = {
    'Saint-Denis': 'saint-denis-97411',
    'Sainte-Marie': 'sainte-marie-97438',
}


class _CityaCardHTMLParser(HTMLParser):
    _VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
             'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.card = None
        self.cards = []

    def handle_starttag(self, tag, attrs):
        attrs_map = {str(key).lower(): value for key, value in attrs}
        if self.card is None:
            item_id = attrs_map.get('data-itemid')
            classes = str(attrs_map.get('class') or '').lower()
            if tag in ('article', 'div') and item_id and 'property-card' in classes:
                self.card = {
                    'id': item_id, 'depth': 1, 'href': None,
                    'ptype': None, 'text': [],
                }
            return
        if tag not in self._VOID:
            self.card['depth'] += 1
        if tag == 'a' and attrs_map.get('href'):
            href = html.unescape(attrs_map['href'])
            match = re.search(
                r'/annonces/location/(appartement|maison)/[^?#]+/'
                + re.escape(self.card['id']) + r'(?:[/?#]|$)',
                href, re.I,
            )
            if match:
                self.card['href'] = href
                self.card['ptype'] = match.group(1).lower()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.card is not None and tag not in self._VOID:
            self.card['depth'] -= 1

    def handle_data(self, data):
        if self.card is not None and data.strip():
            self.card['text'].append(data.strip())

    def handle_endtag(self, tag):
        if self.card is None or tag in self._VOID:
            return
        self.card['depth'] -= 1
        if self.card['depth'] == 0:
            if self.card['href'] and self.card['ptype']:
                self.cards.append((
                    self.card['id'], self.card['href'], self.card['ptype'],
                    ' '.join(self.card['text']),
                ))
            self.card = None


def _citya_strict_cards(text):
    parser = _CityaCardHTMLParser()
    parser.feed(text or '')
    parser.close()
    return parser.cards


def scrape_citya(max_items=2000, max_pages=50, delay=1.5):
    found = []
    seen_global = set()
    route_states = {}
    signals = []
    pages_attempted = 0
    pages_succeeded = 0
    raw_items = 0
    raw_unique_ids = set()
    rejected_out_of_scope = set()
    missing_id = 0
    rejected_non_card = set()

    for route_city, slug in CITYA_COMMUNES.items():
        for route_ptype in ('appartement', 'maison'):
            route_key = f'{route_city}:{route_ptype}'
            route_ids = set()
            state = {'status': 'partial', 'terminal': None, 'items': 0}
            base = f'https://www.citya.com/annonces/location/{route_ptype}/{slug}'
            for page in range(1, max_pages + 1):
                url = base if page == 1 else f'{base}?page={page}'
                pages_attempted += 1
                try:
                    text, _ = fetch(url)
                except Exception:
                    signals.append('fetch_failure')
                    state['terminal'] = f'fetch_failure:page:{page}'
                    break
                pages_succeeded += 1
                raw_matches = re.findall(
                    r'data-itemId=["\']([A-Z0-9-]+)["\']', text or '', re.I,
                )
                raw_ids = set(raw_matches)
                property_card_nodes = len(re.findall(
                    r'class=["\'][^"\']*\bproperty-card\b', text or '', re.I,
                ))
                missing_id += max(0, property_card_nodes - len(raw_matches))
                if _blocked_or_challenge_page(text):
                    signals.append('blocked_or_challenge_page')
                    state['terminal'] = f'blocked_or_challenge_page:page:{page}'
                    break
                raw_items += len(raw_matches)
                raw_unique_ids.update(raw_ids)
                cards = _citya_strict_cards(text)
                strict_ids = {card[0] for card in cards}
                rejected_non_card.update(raw_ids - strict_ids)
                if raw_ids and not cards:
                    signals.append('selector_mismatch')
                    state['terminal'] = f'selector_mismatch:page:{page}'
                    break
                page_new = 0
                for item_id, href, card_ptype, body in cards:
                    if item_id in route_ids:
                        continue
                    route_ids.add(item_id)
                    card_url = urljoin('https://www.citya.com', href)
                    observed = city_from_url(card_url) or guess_city_from_text(clean(body))
                    card_city = _target_city(observed)
                    if card_ptype != route_ptype or card_city != route_city:
                        rejected_out_of_scope.add(item_id)
                        continue
                    page_new += 1
                    if item_id not in seen_global:
                        seen_global.add(item_id)
                        found.append((route_city, item_id, card_ptype, card_url))
                state['items'] = len(route_ids)
                total = _reported_total(text)
                if total is not None:
                    state['reported_total'] = total
                if total is not None and len(route_ids) >= total:
                    state.update(status='complete', terminal='reported_total')
                    break
                if not raw_ids:
                    state.update(status='complete', terminal='empty_page')
                    break
                if not _has_next_page(text, page + 1):
                    state.update(status='complete', terminal='no_next')
                    break
                if not page_new and cards:
                    signals.append('pagination_stalled')
                    state['terminal'] = f'pagination_stalled:page:{page}'
                    break
                if len(found) >= max_items:
                    signals.append(f'safety_item_cap_reached:{max_items}')
                    state['terminal'] = f'safety_item_cap_reached:{max_items}'
                    break
                if page == max_pages:
                    signals.append(f'safety_page_cap_reached:{max_pages}')
                    state['terminal'] = f'safety_page_cap_reached:{max_pages}'
                    break
                time.sleep(delay)
            route_states[route_key] = state

    accepted_ids = {item_id for _, item_id, _, _ in found}
    rejected_out_of_scope.difference_update(accepted_ids)
    rejected_non_card.difference_update(accepted_ids | rejected_out_of_scope)
    duplicate_raw = max(0, raw_items - len(raw_unique_ids))
    parsed_items = max(0, raw_items - missing_id)
    _set_snapshot_meta(
        'citya', found=[(None, sid, None) for sid in raw_unique_ids],
        route_states=route_states, pages_attempted=pages_attempted,
        pages_succeeded=pages_succeeded, raw_items=raw_items, signals=signals,
        extra={
            'rejected_out_of_scope': len(rejected_out_of_scope),
            'rejected_non_card': len(rejected_non_card),
            'parsed_items': parsed_items,
            'unique_ids': len(raw_unique_ids),
            'unparsed_items_by_reason': (
                {'missing_id': missing_id} if missing_id else {}
            ),
            'pre_unique_rejections_by_reason': (
                {'duplicate_raw': duplicate_raw} if duplicate_raw else {}
            ),
        },
    )
    out = []
    detail_failures = set()
    detail_scope_rejections = set()
    for card_city, item_id, ptype, url in found[:max_items]:
        try:
            listing = detail_listing(
                'citya', url, 'house' if ptype == 'maison' else 'flat', item_id,
            )
        except Exception:
            detail_failures.add(item_id)
            continue
        observed = listing.city
        observed_target = _target_city(observed)
        if observed and observed_target != card_city:
            detail_scope_rejections.add(item_id)
            continue
        # The city comes from the listing-card URL, not from the queried route.
        out.append(replace(listing, city=observed_target or card_city))
        time.sleep(delay)
    unprocessed_cap = accepted_ids - {item.source_id for item in out}
    unprocessed_cap.difference_update(detail_failures | detail_scope_rejections)
    if len(found) <= max_items:
        unprocessed_cap.clear()
    rejection_reasons = {
        reason: len(ids) for reason, ids in {
            'out_of_scope': rejected_out_of_scope | detail_scope_rejections,
            'missing_card_evidence': rejected_non_card,
            'detail_fetch_failure': detail_failures,
            'safety_item_cap': unprocessed_cap,
        }.items() if ids
    }
    SOURCE_RUNTIME_META['citya'].update(
        detail_fetch_failures=len(detail_failures),
        rejected_out_of_scope=len(rejected_out_of_scope | detail_scope_rejections),
    )
    _merge_rejection_reasons('citya', rejection_reasons)
    if detail_failures:
        _add_runtime_signal('citya', 'detail_fetch_failure')
    return out

# Trouve le 27/07 (soir) : `offset` est ignore par l'API Keldom (teste :
# offset=200 renvoie le meme set que limit=200 sans offset -- ce n'est pas
# une vraie pagination page/offset). Le vrai levier est `limit` seul :
# 200->500 triple le nombre d'offres Reunion utiles apres filtre. Au-dela
# de ~700-1000, l'API renvoie 500 (verifie : 1000 et 3000 echouent).
DOMIMMO_NON_RESIDENTIAL = ('local commercial', 'bureau', 'entrepot', 'entrepôt', 'terrain', 'fonds de commerce', 'parking', 'garage')
DOMIMMO_RESIDENTIAL = ('appartement', 'studio', 'maison', 'villa', 'duplex', 't1', 't2', 't3', 't4', 't5')


def is_domimmo_residential(title, description):
    hay = clean((title or '') + ' ' + (description or '')).lower()
    if any(token in hay for token in DOMIMMO_NON_RESIDENTIAL):
        return False
    return any(token in hay for token in DOMIMMO_RESIDENTIAL)


# --- Leboncoin via Apify actor piotrv1001/leboncoin-listings-scraper ---------
# Leboncoin blocks plain scraping, so we go through the Apify actor. The actor's
# default dataset (native leboncoin ad objects) is what we map here. Two modes:
#   * APIFY_LEBONCOIN_DATASET_ID set  -> read that existing dataset, no actor run
#     (cheap, reproducible: reuse a run already paid for).
#   * otherwise -> run the actor synchronously and read its default dataset.
# The Apify token (APIFY_TOKEN) is sent ONLY in the Authorization header, never
# in a URL nor in FETCH_LOG -- so it cannot leak into logs/summary output.
APIFY_BASE = 'https://api.apify.com/v2'
DEFAULT_LEBONCOIN_ACTOR = 'scrapifier~leboncoin-universal-scraper'
# Scope vivant arbitre par Moufadal: Saint-Denis + Sainte-Marie seulement.
LEBONCOIN_COMMUNES = [
    ('Saint-Denis', '97400'), ('Sainte-Marie', '97438'),
]
LEBONCOIN_SNAPSHOT_MAX_PAGES = 20
LEBONCOIN_SNAPSHOT_LIMIT_PER_PAGE = 35
LEBONCOIN_DATASET_CAPACITY = (
    len(LEBONCOIN_COMMUNES) * LEBONCOIN_SNAPSHOT_MAX_PAGES * LEBONCOIN_SNAPSHOT_LIMIT_PER_PAGE
)
# Leboncoin real_estate_type: 1=Maison, 2=Appartement (residentiel);
# 3=Terrain, 4=Parking, 5=Autre (hors perimetre veille locative residentielle).
LEBONCOIN_RESIDENTIAL_TYPE_VALUES = {'1', '2'}
LEBONCOIN_RESIDENTIAL_TYPE_LABELS = ('maison', 'villa', 'appartement', 'studio', 'duplex')


def _to_int_or_none(v):
    if v in (None, ''):
        return None
    try:
        return int(float(str(v).replace(',', '.')))
    except Exception:
        return to_int_price(v)


def _to_float_or_none(v):
    if v in (None, ''):
        return None
    try:
        return float(str(v).replace(',', '.'))
    except Exception:
        return parse_surface(v)


def _lbc_attr(item, key):
    """Read a leboncoin attribute by key.

    Supports the real piotrv1001 shape (`attributes` is a flat dict), the native
    list shape (`[{key,value,value_label}]`), and already-flattened top-level
    fields.
    """
    val = item.get(key)
    if val not in (None, '') and not isinstance(val, (dict, list)):
        return val
    attrs = item.get('attributes')
    if isinstance(attrs, dict):
        v = attrs.get(key)
        return v if v not in (None, '') else None
    for a in attrs or []:
        if isinstance(a, dict) and a.get('key') == key:
            v = a.get('value')
            return v if v not in (None, '') else a.get('value_label')
    return None


def _lbc_attr_label(item, key):
    attrs = item.get('attributes')
    if isinstance(attrs, dict):
        return None
    for a in attrs or []:
        if isinstance(a, dict) and a.get('key') == key:
            return a.get('value_label') or a.get('value')
    lab = item.get(f'{key}_label')
    return lab if lab not in (None, '') else None


def _lbc_property_type(value, label):
    v = str(value or '').strip()
    # Some Apify shapes expose real_estate_type directly as a label
    # ("Appartement") instead of the native numeric code ("2"). Treat both
    # representations as the same source-of-truth field.
    lab = f'{label or ""} {v}'.lower()
    if v == '1' or 'maison' in lab or 'villa' in lab:
        return 'house'
    if v == '2' or any(x in lab for x in ('appartement', 'studio', 'duplex')):
        return 'flat'
    return None


def _lbc_price(item):
    p = item.get('price')
    if isinstance(p, list):
        p = p[0] if p else None
    val = to_int_price(p)
    if val is None:
        cents = item.get('price_cents')
        try:
            val = int(round(int(cents) / 100)) if cents not in (None, '') else None
        except Exception:
            val = None
    return val


def _lbc_image(item):
    imgs = item.get('images')
    if isinstance(imgs, dict):
        urls = imgs.get('urls_large') or imgs.get('urls') or imgs.get('urls_thumb')
        if isinstance(urls, list) and urls:
            return urls[0]
        if imgs.get('thumb_url'):
            return imgs.get('thumb_url')
    if isinstance(imgs, list) and imgs:
        first = imgs[0]
        if isinstance(first, dict):
            for k in ('largeUrl', 'imageUrl', 'url', 'smallUrl', 'thumbnailUrl'):
                if first.get(k):
                    return first.get(k)
        elif first:
            return str(first)
    for k in ('image', 'image_url', 'thumbnail'):
        v = item.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _leboncoin_rejection_reason(item):
    if not isinstance(item, dict):
        return 'non_object'
    ret_value = (_lbc_attr(item, 'real_estate_type')
                 or _lbc_attr(item, 'realEstateType'))
    ret_label = (_lbc_attr_label(item, 'real_estate_type')
                 or _lbc_attr_label(item, 'realEstateType'))
    value = str(ret_value).strip() if ret_value is not None else ''
    label = f'{ret_label or ""} {value}'.lower()
    if not (
        value in LEBONCOIN_RESIDENTIAL_TYPE_VALUES
        or any(token in label for token in LEBONCOIN_RESIDENTIAL_TYPE_LABELS)
    ):
        return 'non_residential'
    native_id = item.get('listId') or item.get('list_id') or item.get('id') or item.get('ad_id')
    if native_id in (None, ''):
        return 'missing_id'
    location = item.get('location') if isinstance(item.get('location'), dict) else {}
    zipcode = clean(location.get('zipcode') or location.get('postal_code') or item.get('zipcode'))
    if not zipcode or not zipcode.startswith('974'):
        return 'non_974'
    return None

def _map_leboncoin_item(item):
    """Map one Apify leboncoin dataset item to a Listing, or None when the ad is
    not a residential rental (keeps land/parking/commercial out). Handles both
    if _leboncoin_rejection_reason(item) is not None:
        return None
    the native leboncoin ad shape (attributes list) and a flattened item shape."""
    if not isinstance(item, dict):
        return None
    ret_value = (_lbc_attr(item, 'real_estate_type')
                 or _lbc_attr(item, 'realEstateType'))
    ret_label = (_lbc_attr_label(item, 'real_estate_type')
                 or _lbc_attr_label(item, 'realEstateType'))
    v = str(ret_value).strip() if ret_value is not None else ''
    lab = f'{ret_label or ""} {v}'.lower()
    residential = v in LEBONCOIN_RESIDENTIAL_TYPE_VALUES or any(
        x in lab for x in LEBONCOIN_RESIDENTIAL_TYPE_LABELS)
    if not residential:
        return None
    native_id = (item.get('listId') or item.get('list_id')
                 or item.get('id') or item.get('ad_id'))
    if native_id in (None, ''):
        return None
    sid = str(native_id)
    url = item.get('url') or item.get('link') or f'https://www.leboncoin.fr/ad/locations/{sid}'
    title = clean(item.get('subject') or item.get('title'))
    desc = clean(item.get('body') or item.get('description'))
    loc = item.get('location') if isinstance(item.get('location'), dict) else {}
    zipcode = clean(loc.get('zipcode') or loc.get('postal_code') or item.get('zipcode'))
    # Scrapifier can confuse same-named mainland cities (Saint-Denis 93). A
    # Leboncoin row is never allowed into the Reunion DB without a 974 postcode.
    if not zipcode or not zipcode.startswith('974'):
        return None
    city = clean(loc.get('city') or item.get('city'))
    district = clean(loc.get('district') or loc.get('city_label')) or None
    ptype = _lbc_property_type(v, lab)
    rooms = _to_int_or_none(_lbc_attr(item, 'rooms') or _lbc_attr(item, 'nb_rooms'))
    bedrooms = _to_int_or_none(_lbc_attr(item, 'bedrooms') or _lbc_attr(item, 'nb_bedrooms'))
    surface = _to_float_or_none(_lbc_attr(item, 'square') or item.get('surface') or item.get('surface_m2'))
    rent = _lbc_price(item)
    published = (item.get('firstPublicationDate') or item.get('first_publication_date')
                 or item.get('index_date') or item.get('publication_date')
                 or item.get('published_at'))
    owner = item.get('owner') if isinstance(item.get('owner'), dict) else {}
    seller = item.get('seller') if isinstance(item.get('seller'), dict) else {}
    agency = (clean(_lbc_attr(item, 'store_name')) or clean(owner.get('name'))
              or clean(seller.get('name'))
              or (owner.get('type') or seller.get('type') or None))
    image = _lbc_image(item)
    charges = to_int_price(_lbc_attr(item, 'monthly_charges'))
    d = {'source': 'apify:leboncoin', 'list_id': sid, 'url': url, 'title': title,
         'city': city, 'price': rent, 'square': _lbc_attr(item, 'square'),
         'rooms': rooms, 'bedrooms': bedrooms, 'real_estate_type': v,
         'published_at': published, 'image': image,
         'agency_or_owner': agency, 'seller': seller, 'owner': owner,
         'attributes': item.get('attributes'), 'raw': item}
    return Listing('leboncoin', sid, url, url, title, city, district, ptype,
                   rooms, bedrooms, surface, rent, charges, agency, published,
                   image, desc, save_raw('leboncoin', sid, d), hash_listing(d))


def _leboncoin_listings(items):
    out = []
    rejected = {}
    for item in items or []:
        reason = _leboncoin_rejection_reason(item)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        listing = _map_leboncoin_item(item)
        if listing is not None:
            out.append(listing)
    if 'leboncoin' in SOURCE_RUNTIME_META:
        _merge_rejection_reasons('leboncoin', rejected)
    return out


def _apify_json(url, token, payload=None, timeout=180):
    """Minimal Apify REST call. Token travels ONLY in the Authorization header;
    it is deliberately not passed through fetch()/FETCH_LOG so it can never leak
    into logs or the printed summary."""
    headers = {'User-Agent': UA, 'Accept': 'application/json',
               'Authorization': f'Bearer {token}'}
    body = None
    method = 'GET'
    if payload is not None:
        body = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
        method = 'POST'
    req = Request(url, data=body, headers=headers, method=method)
    with urlopen(req, timeout=timeout, context=CTX) as r:
        raw = r.read().decode('utf-8', 'replace')
    return json.loads(raw) if raw.strip() else []


def _apify_cost_usd(run):
    """Best-effort Apify cost extraction; API shapes vary by endpoint/account."""
    if not isinstance(run, dict):
        return None
    for key in ('usageTotalUsd', 'usageUsd', 'costUsd', 'totalCostUsd'):
        val = run.get(key)
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                pass
    usage = run.get('usage')
    if isinstance(usage, dict):
        val = usage.get('totalUsd') or usage.get('totalCostUsd')
        if isinstance(val, (int, float)):
            return float(val)
    return None


def _write_apify_usage(*, mode, result_count, dataset_id=None, run=None, actor=None):
    """Append one Apify accounting row, parallel to llm_extraction.jsonl."""
    if not APIFY_USAGE_REPORT:
        return
    run = run if isinstance(run, dict) else {}
    row = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'provider': 'apify',
        'source': 'leboncoin',
        'mode': mode,
        'actor': actor,
        'run_id': run.get('id'),
        'dataset_id': dataset_id or run.get('defaultDatasetId'),
        'result_count': result_count,
        'cost_usd': _apify_cost_usd(run),
    }
    Path(APIFY_USAGE_REPORT).parent.mkdir(parents=True, exist_ok=True)
    with open(APIFY_USAGE_REPORT, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def _leboncoin_actor_input(max_items):
    """Bounded full-snapshot input: 700 slots for the two live communes."""
    return {
        'urls_list': [
            ('https://www.leboncoin.fr/recherche?category=10'
             f'&locations={c}_{z}&real_estate_type=1,2')
            for c, z in LEBONCOIN_COMMUNES
        ],
        'max_pages': LEBONCOIN_SNAPSHOT_MAX_PAGES,
        'limit_per_page': LEBONCOIN_SNAPSHOT_LIMIT_PER_PAGE,
        'delay_between_pages': 1,
        'max_age_days': 0,
        'proxyConfiguration': {
            'useApifyProxy': True,
            'apifyProxyGroups': ['RESIDENTIAL'],
            'apifyProxyCountry': 'FR',
        },
    }


def _leboncoin_runtime_meta(items, *, dataset_id, mode, max_items):
    """Prove a full actor snapshot without inventing per-page telemetry."""
    raw_ids = {
        str(item.get('listId') or item.get('list_id') or item.get('id') or item.get('ad_id'))
        for item in items if isinstance(item, dict)
        and (item.get('listId') or item.get('list_id') or item.get('id') or item.get('ad_id')) not in (None, '')
    }
    by_city = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        location = item.get('location') if isinstance(item.get('location'), dict) else {}
        city = clean(location.get('city') or item.get('city')) or 'unknown'
        by_city[city] = by_city.get(city, 0) + 1
    signals = []
    if mode != 'actor_run':
        signals.append('dataset_reuse_unverified')
    if max_items < LEBONCOIN_DATASET_CAPACITY:
        signals.append(f'dataset_capacity_too_small:{max_items}')
    if len(items) >= max_items:
        signals.append(f'dataset_limit_reached:{max_items}')
    per_commune_cap = LEBONCOIN_SNAPSHOT_MAX_PAGES * LEBONCOIN_SNAPSHOT_LIMIT_PER_PAGE
    if any(count >= per_commune_cap for count in by_city.values()):
        signals.append('per_commune_page_cap_reached')
    expected_cities = {city for city, _ in LEBONCOIN_COMMUNES}
    missing_cities = sorted(expected_cities - set(by_city))
    if missing_cities:
        signals.append('expected_city_missing:' + ','.join(missing_cities))
    full_snapshot_proof = mode == 'actor_run' and not signals
    return {
        'raw_items': len(items), 'unique_ids': len(raw_ids), 'dataset_id': dataset_id,
        # Apify actor status is the observed unit.  Do not claim 20 page successes
        # when the actor API did not expose per-page stats.
        'pages_attempted': 1 if mode == 'actor_run' else 0,
        'pages_succeeded': 1 if mode == 'actor_run' else 0,
        'max_pages_requested': LEBONCOIN_SNAPSHOT_MAX_PAGES,
        'limit_per_page_requested': LEBONCOIN_SNAPSHOT_LIMIT_PER_PAGE,
        'dataset_limit': max_items,
        'observed_cities': sorted(by_city),
        'truncation_signals': signals,
        'full_snapshot_proof': full_snapshot_proof,
        'snapshot_proof': 'apify_actor_succeeded_non_saturated_all_expected_cities' if full_snapshot_proof else None,
        'rejected_items_by_reason': {},
    }


def _leboncoin_actor_snapshot(*, token, actor, max_items):
    """Run the actor once and account for exactly that run."""
    run_url = (f'{APIFY_BASE}/acts/{quote(actor, safe="~")}/runs'
               f'?waitForFinish=180')
    run = _apify_json(run_url, token, payload=_leboncoin_actor_input(max_items))
    if isinstance(run, dict) and isinstance(run.get('data'), dict):
        run = run['data']
    dataset_id = run.get('defaultDatasetId') if isinstance(run, dict) else None
    run_status = str(run.get('status') or '').upper() if isinstance(run, dict) else ''
    if run_status != 'SUCCEEDED':
        _write_apify_usage(mode='actor_run', actor=actor, dataset_id=dataset_id,
                           run=run, result_count=0)
        raise RuntimeError(f'Apify run not successful: status={run_status or "missing"}')
    if not dataset_id:
        _write_apify_usage(mode='actor_run', actor=actor, dataset_id=None,
                           run=run, result_count=0)
        raise RuntimeError('Apify run finished without defaultDatasetId')
    url = (f'{APIFY_BASE}/datasets/{quote(dataset_id, safe="")}/items'
           f'?clean=true&format=json&limit={max_items}')
    try:
        items = _apify_json(url, token)
    except Exception:
        _write_apify_usage(mode='actor_run', actor=actor, dataset_id=dataset_id,
                           run=run, result_count=0)
        raise
    if isinstance(items, dict):
        items = items.get('items') or items.get('data') or []
    items = items if isinstance(items, list) else []
    _write_apify_usage(mode='actor_run', actor=actor, dataset_id=dataset_id,
                       run=run, result_count=len(items))
    return items, dataset_id


def scrape_leboncoin_apify_dataset():
    """Leboncoin residential rentals via Apify (Scrapifier universal scraper).

    Env:
      APIFY_TOKEN              (required) Apify API token, sent as Bearer header only.
      APIFY_LEBONCOIN_DATASET_ID (opt) read an existing dataset instead of running.
      APIFY_LEBONCOIN_MAX_ITEMS  (opt) default 40.
      APIFY_LEBONCOIN_ACTOR      (opt) override actor id (default piotrv1001~...).
    Non-residential ads are filtered out on the result; source_site is 'leboncoin'
    and source_id is the native leboncoin list_id (stable across runs).
    """
    token = os.environ.get('APIFY_TOKEN', '').strip()
    dataset_id = os.environ.get('APIFY_LEBONCOIN_DATASET_ID', '').strip()
    try:
        max_items = int(os.environ.get('APIFY_LEBONCOIN_MAX_ITEMS', '') or LEBONCOIN_DATASET_CAPACITY)
    except ValueError:
        max_items = LEBONCOIN_DATASET_CAPACITY
    if not token:
        raise RuntimeError('APIFY_TOKEN missing: cannot query Apify (leboncoin)')
    if dataset_id:
        url = (f'{APIFY_BASE}/datasets/{quote(dataset_id, safe="")}/items'
               f'?clean=true&format=json&limit={max_items}')
        items = _apify_json(url, token)
        mode = 'dataset'
        actor = os.environ.get('APIFY_LEBONCOIN_ACTOR', '').strip() or DEFAULT_LEBONCOIN_ACTOR
        if isinstance(items, dict):
            items = items.get('items') or items.get('data') or []
        items = items if isinstance(items, list) else []
        _write_apify_usage(mode=mode, actor=actor, dataset_id=dataset_id,
                           run=None, result_count=len(items))
        runtime_meta = _leboncoin_runtime_meta(
            items, dataset_id=dataset_id, mode=mode, max_items=max_items,
        )
        runtime_meta['retries'] = 0
    else:
        actor = os.environ.get('APIFY_LEBONCOIN_ACTOR', '').strip() or DEFAULT_LEBONCOIN_ACTOR
        mode = 'actor_run'
        items, dataset_id = _leboncoin_actor_snapshot(
            token=token, actor=actor, max_items=max_items,
        )
        runtime_meta = _leboncoin_runtime_meta(
            items, dataset_id=dataset_id, mode=mode, max_items=max_items,
        )
        runtime_meta['retries'] = 0
        if items and not runtime_meta['full_snapshot_proof']:
            first_items = items
            first_dataset_id = dataset_id
            first_meta = runtime_meta
            try:
                items, dataset_id = _leboncoin_actor_snapshot(
                    token=token, actor=actor, max_items=max_items,
                )
            except Exception as exc:
                items = first_items
                dataset_id = first_dataset_id
                runtime_meta = first_meta
                runtime_meta['retries'] = 1
                signals = list(runtime_meta.get('truncation_signals') or [])
                signals.append(f'actor_retry_failed:{type(exc).__name__}')
                runtime_meta['truncation_signals'] = list(dict.fromkeys(signals))
                runtime_meta['retry_error'] = f'{type(exc).__name__}: {exc}'
                runtime_meta['full_snapshot_proof'] = False
                runtime_meta['snapshot_proof'] = None
            else:
                runtime_meta = _leboncoin_runtime_meta(
                    items, dataset_id=dataset_id, mode=mode, max_items=max_items,
                )
                runtime_meta['retries'] = 1
                if not runtime_meta['full_snapshot_proof']:
                    signals = list(runtime_meta.get('truncation_signals') or [])
                    signals.append('actor_retry_still_partial')
                    runtime_meta['truncation_signals'] = list(dict.fromkeys(signals))
    SOURCE_RUNTIME_META['leboncoin'] = runtime_meta
    if not items:
        raise RuntimeError('Apify dataset empty: leboncoin source produced no listings')
    return _leboncoin_listings(items)


# --- Adrezio (agence Reunion) : pages liste statiques, fetch() HTTP simple -----
# Trouve : Adrezio publie ses locations sur des pages de liste par commune et par
# type, /reunion/location/{appartement,maison}/{commune-slug}?page=N. Le HTML est
# statique (pas de rendu JS) -> fetch() urllib suffit, PAS de Playwright/CDP. La
# commune interrogee est le signal SUR (comme citya/97immo), on l'impose sur le
# resultat plutot que de la deviner depuis le texte de detail.
ADREZIO_BASE = 'https://adrezio.fr'
ADREZIO_TYPES = {'appartement': 'flat', 'maison': 'house'}


def _adrezio_card_listings(text, ptype, commune, seen):
    """Extract Adrezio listings from search-result cards only.

    Measured 2026-08-03: detail pages are fast to fetch (~1.4s) but the generic
    detail parser can spend ~80s in image regexes on Next/React HTML. The list
    cards already carry URL, title, price, surface, rooms, city and image, so this
    intentionally avoids every detail-page fetch.
    """
    out = []
    for m in re.finditer(r'<a\b[^>]*href=["\'](/annonces/[a-z0-9]+)["\'][\s\S]*?</a>', text or '', re.I):
        href = html.unescape(m.group(1))
        url = urljoin(ADREZIO_BASE, href)
        if url in seen:
            continue
        card = m.group(0)
        sid = url.rstrip('/').split('/')[-1]
        alt_m = re.search(r'<img\b[^>]*\balt=["\']([^"\']+)["\']', card, re.I | re.S)
        alt = clean(alt_m.group(1)) if alt_m else None
        title = None
        if alt:
            title = re.sub(r'^Photo\s+\d+\s*-\s*', '', alt, flags=re.I).strip() or alt
        card_text = clean(card) or title or ''
        span_texts = [clean(x) for x in re.findall(r'<span\b[^>]*>(.*?)</span>', card, re.I | re.S)]
        span_texts = [x for x in span_texts if x]
        structured_text = ' '.join(span_texts)
        # Prefer structured card spans for compact facts (surface/rooms), then
        # title/alt, then whole card text. Price is usually visible text near the
        # city; title remains a safe fallback for non-breaking-space variants.
        title_text = title or card_text
        rent = parse_rent_eur(card_text) or parse_rent_eur(title_text)
        surface = parse_surface(structured_text) or parse_surface(title_text) or parse_surface(card_text)
        rooms = parse_rooms(structured_text) or parse_rooms(title_text) or parse_rooms(card_text)
        bedrooms = None
        bed_m = re.search(r'([1-9])\s*chambres?', title_text or card_text, re.I)
        if bed_m:
            bedrooms = int(bed_m.group(1))
        city = guess_city_from_text(title_text) or guess_city_from_text(card_text) or commune
        img = None
        img_m = re.search(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', card, re.I | re.S)
        if img_m:
            img = normalize_image_url(html.unescape(img_m.group(1)).replace('\\/', '/'), ADREZIO_BASE)
        if not img:
            srcset_m = re.search(r'<(?:img|source)\b[^>]*\b(?:srcset|data-srcset)=["\']([^"\']+)["\']', card, re.I | re.S)
            if srcset_m:
                img = normalize_image_url(first_srcset_url(html.unescape(srcset_m.group(1))) or '', ADREZIO_BASE)
        raw = {'url': url, 'title': title_text, 'description': card_text[:900],
               'image': img, 'image_url': img,
               'city': city, 'rent_eur': rent, 'rent': rent,
               'surface_m2': surface, 'surface': surface,
               'rooms': rooms, 'bedrooms': bedrooms, 'property_type': ptype,
               'queried_commune': commune, 'extraction': 'adrezio_list_card'}
        seen.add(url)
        out.append(Listing('adrezio', sid, url, url, title_text, city, None, ptype,
                           rooms, bedrooms, surface, rent, None, None, None, img,
                           card_text[:900], save_raw('adrezio', sid, raw), hash_listing(raw)))
    return out


# --- Authoritative two-city snapshot adapters ---------------------------------
# These definitions deliberately supersede the historical whole-island adapters
# above. Each adapter publishes machine-checkable exhaustion and rejection proof.
IMMO974_CITY_ROUTES = {
    city: 'https://www.immo974.com/resultat-de-recherche?' + urlencode({
        'a': 'dosearch', 'regions[0][city]': f'{city.replace("-", " ")} ({postal})',
        'searchcategory': '2',
    })
    for city, postal in (('Saint-Denis', '97400'), ('Sainte-Marie', '97438'))
}
LOCAMOI_CITY_TYPE_ROUTES = {
    f'{city}:{ptype}': f'https://locamoi.fr/location/{slug}/la-reunion-{city_slug}'
    for city, city_slug in (
        ('Saint-Denis', 'saint-denis'), ('Sainte-Marie', 'sainte-marie'),
    )
    for ptype, slug in (('flat', 'appartement'), ('house', 'maison'))
}
IMMO97_COMMUNES = {'Saint-Denis': '195', 'Sainte-Marie': '82'}


def _article_blocks(text):
    return re.findall(r'<article\b[^>]*>(.*?)</article>', text or '', re.I | re.S)


def _immo974_item_id(article):
    match = re.search(r'href=["\']([^"\']*/annonce/locations/[^"\']+)["\']', article, re.I)
    if not match:
        return None
    url = html.unescape(match.group(1))
    match = re.search(r'-([A-Za-z0-9]+)\.html(?:[?#]|$)', url)
    return match.group(1) if match else hashlib.md5(url.encode()).hexdigest()[:16]


def _page_query(base, page, *, parameter='page'):
    if page == 1:
        return base
    return base + ('&' if '?' in base else '?') + urlencode({parameter: page})


def _complete_adapter_rejections(source, reasons):
    _merge_rejection_reasons(source, {
        reason: len(ids) for reason, ids in reasons.items() if ids
    })


def scrape_immo974(max_pages=50, page_size=20, max_items=5000, delay=1.5):
    found = _walk_target_routes(
        source='immo974', routes=IMMO974_CITY_ROUTES,
        max_pages=max_pages, max_items=max_items, delay=delay,
        page_url=lambda base, page: (
            base if page == 1 else base + '&' + urlencode({
                'offset': (page - 1) * page_size, 'results': page_size,
            })
        ),
        parse_items=_article_blocks, item_key=_immo974_item_id,
    )
    out = []
    rejected = {'mapping_failure': set(), 'non_residential': set(), 'out_of_scope': set()}
    for route_city, sid, article in found:
        url_match = re.search(r'href=["\']([^"\']*/annonce/locations/[^"\']+)["\']', article, re.I)
        if not url_match:
            rejected['mapping_failure'].add(sid)
            continue
        url = urljoin('https://www.immo974.com/', html.unescape(url_match.group(1)))
        low_url = url.lower()
        ptype = 'flat' if 'appartement' in low_url else (
            'house' if any(value in low_url for value in ('maison', 'villa')) else None
        )
        if not ptype:
            rejected['non_residential'].add(sid)
            continue
        title_match = re.search(r'<h2 class=["\']ville-type["\']>\s*<a[^>]*title=["\']([^"\']+)', article, re.I | re.S)
        city_match = re.search(r'<h2 class=["\']localisation["\'][^>]*>.*?</i>\s*(.*?)\s*</h2>', article, re.I | re.S)
        price_match = re.search(r'<div class=["\']price-result["\'][^>]*>\s*<b>\s*([^<]+)', article, re.I | re.S)
        date_match = re.search(r'<div class=["\']date_publication["\'][^>]*>\s*([^<]+)', article, re.I | re.S)
        desc_match = re.search(r'<p class=["\']description["\'][^>]*>(.*?)</p>', article, re.I | re.S)
        title = clean(title_match.group(1)) if title_match else None
        observed_city = clean(city_match.group(1)) if city_match else None
        city = _target_city(observed_city)
        if city != route_city:
            rejected['out_of_scope'].add(sid)
            continue
        description = clean(desc_match.group(1)) if desc_match else None
        image = extract_image_url(article, 'https://www.immo974.com/')
        data = {
            'url': url, 'title': title, 'city': observed_city,
            'price': clean(price_match.group(1)) if price_match else None,
            'date': clean(date_match.group(1)) if date_match else None,
            'description': description, 'image': image,
        }
        joined = ' '.join(value for value in (title, description) if value)
        out.append(Listing(
            'immo974', sid, url, url, title, city, None, ptype,
            parse_rooms(joined), None, parse_surface(joined),
            to_int_price(data['price']), None, None, data['date'], image,
            description, save_raw('immo974', sid, data), hash_listing(data),
        ))
    _complete_adapter_rejections('immo974', rejected)
    return out


def _locamoi_items(text):
    items = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text or '', re.I | re.S):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            main = obj.get('mainEntity') or obj
            elements = main.get('itemListElement', []) if isinstance(main, dict) else []
            if isinstance(elements, list):
                items.extend(item for item in elements if isinstance(item, dict))
    return items


def _locamoi_item_id(element):
    item = element.get('item') if isinstance(element.get('item'), dict) else {}
    offers = item.get('offers') if isinstance(item.get('offers'), dict) else {}
    return item.get('url') or offers.get('url')


def scrape_locamoi(max_pages=50, max_items=5000, delay=1.5):
    found = _walk_target_routes(
        source='locamoi', routes=LOCAMOI_CITY_TYPE_ROUTES,
        max_pages=max_pages, max_items=max_items, delay=delay,
        page_url=_page_query, parse_items=_locamoi_items,
        item_key=_locamoi_item_id,
    )
    out = []
    rejected = {'mapping_failure': set(), 'out_of_scope': set()}
    for route_key, key, element in found:
        route_city, ptype = route_key.split(':', 1)
        item = element.get('item') if isinstance(element.get('item'), dict) else {}
        offers = item.get('offers') if isinstance(item.get('offers'), dict) else {}
        offered = offers.get('itemOffered') if isinstance(offers.get('itemOffered'), dict) else {}
        address = offered.get('address') if isinstance(offered.get('address'), dict) else {}
        url = item.get('url') or offers.get('url')
        if not url:
            rejected['mapping_failure'].add(key)
            continue
        city = _target_city(address.get('addressLocality'))
        if city != route_city:
            rejected['out_of_scope'].add(key)
            continue
        sid = url.rstrip('/').split('/')[-1]
        title = clean(item.get('name'))
        floor = offered.get('floorSize')
        surface = floor.get('value') if isinstance(floor, dict) else floor
        beds = offered.get('numberOfBedrooms')
        bedrooms = beds.get('value') if isinstance(beds, dict) else beds
        room_data = offered.get('numberOfRooms')
        rooms = room_data.get('value') if isinstance(room_data, dict) else room_data
        if rooms in (None, ''):
            room_match = re.search(r'\b[TF]\s*([1-9])\b', title or '', re.I)
            rooms = int(room_match.group(1)) if room_match else parse_rooms(title)
        image = item.get('image')
        data = {
            'url': url, 'title': title, 'price': offers.get('price'),
            'city': address.get('addressLocality'), 'surface': surface,
            'rooms': rooms, 'bedrooms': bedrooms, 'image': image,
        }
        out.append(Listing(
            'locamoi', sid, url, url, title, city, None, ptype,
            int(rooms) if isinstance(rooms, (int, float)) else parse_rooms(title),
            int(bedrooms) if isinstance(bedrooms, (int, float)) else None, float(surface) if isinstance(surface, (int, float)) else parse_surface(title),
            int(offers['price']) if isinstance(offers.get('price'), (int, float)) else to_int_price(offers.get('price')),
            None, 'locamoi/aggregated', offers.get('validFrom'), image, title,
            save_raw('locamoi', sid, data), hash_listing(data),
        ))
    _complete_adapter_rejections('locamoi', rejected)
    return out


def _immo97_links(text):
    return unique_links(
        text,
        r'href=["\']([^"\']*/immobilier-annonce/location/[^"\']+)["\']',
        'https://www.97immo.com/', 5000,
    )


def scrape_97immo(max_items=5000, max_pages=50, delay=1.5):
    routes = {
        city: (
            'https://www.97immo.com/immo_liste_location.php?'
            + urlencode({
                'id_typeoffres': 'location', 'id_destinations': '19',
                'id_localisations[]': cid, 'typelien': 'moteur_search',
            })
        )
        for city, cid in IMMO97_COMMUNES.items()
    }
    found = _walk_target_routes(
        source='97immo', routes=routes, max_pages=max_pages,
        max_items=max_items, delay=delay, page_url=_page_query,
        parse_items=_immo97_links, item_key=lambda url: url,
    )
    out = []
    rejected = {
        'non_residential': set(), 'out_of_scope': set(),
        'detail_fetch_failure': set(),
    }
    for route_city, url, _ in found:
        match = re.search(r'/immobilier-annonce/location/(appartement|maison(?:-villa)?)/', url, re.I)
        if not match:
            rejected['non_residential'].add(url)
            continue
        ptype = 'house' if match.group(1).lower().startswith('maison') else 'flat'
        parts = url.rstrip('/').split('/')
        sid = '_'.join(parts[-2:])
        try:
            listing = detail_listing('97immo', url, ptype, sid)
        except Exception:
            rejected['detail_fetch_failure'].add(url)
            continue
        city = _target_city(listing.city)
        if listing.city and city != route_city:
            rejected['out_of_scope'].add(url)
            continue
        out.append(replace(listing, city=city or route_city))
        time.sleep(delay)
    _complete_adapter_rejections('97immo', rejected)
    if rejected['detail_fetch_failure']:
        _add_runtime_signal('97immo', 'detail_fetch_failure')
    return out


def _ofim_catalogue_links(text):
    return unique_links(
        text,
        r'href=["\'](https://www\.ofim\.fr/\d+/Location-[^"\']+)["\']',
        'https://www.ofim.fr/', 5000,
    )


def scrape_ofim(max_items=5000, max_pages=50, delay=1.5):
    found = []
    all_unique = set()
    raw_items = 0
    duplicate_raw = 0
    safety_rejected = set()
    route_states = {}
    signals = []
    pages_attempted = 0
    pages_succeeded = 0
    cap_reached = False
    for slug, ptype in OFIM_CATEGORIES.items():
        state = {'status': 'partial', 'terminal': None, 'items': 0}
        route_ids = set()
        if cap_reached:
            state['terminal'] = f'safety_item_cap_reached:{max_items}'
            route_states[slug] = state
            continue
        seed_url = f'https://www.ofim.fr/liste-location-{slug}.html'
        pages_attempted += 1
        try:
            text, _ = fetch(seed_url)
        except Exception:
            signals.append('fetch_failure')
            state['terminal'] = 'fetch_failure:seed'
            route_states[slug] = state
            continue
        pages_succeeded += 1
        if _blocked_or_challenge_page(text):
            signals.append('blocked_or_challenge_page')
            state['terminal'] = 'blocked_or_challenge_page:seed'
            route_states[slug] = state
            continue
        rc1_match = re.search(r'rc1=(\d+)', text)
        page_number = 0
        while True:
            links = _ofim_catalogue_links(text)
            raw_items += len(links)
            for url in links:
                if url in route_ids:
                    duplicate_raw += 1
                    continue
                route_ids.add(url)
                if url in all_unique:
                    duplicate_raw += 1
                    continue
                all_unique.add(url)
                if len(found) < max_items:
                    found.append((url, ptype))
                else:
                    safety_rejected.add(url)
                    cap_reached = True
            state['items'] = len(route_ids)
            total = _reported_total(text)
            if total is not None:
                state['reported_total'] = total
            if total is not None and len(route_ids) >= total:
                state.update(status='complete', terminal='reported_total')
                break
            if cap_reached:
                signal = f'safety_item_cap_reached:{max_items}'
                signals.append(signal)
                state['terminal'] = signal
                break
            if page_number == 0 and not rc1_match:
                if _has_next_page(text, 2):
                    signals.append('pagination_cursor_missing')
                    state['terminal'] = 'pagination_cursor_missing'
                else:
                    state.update(status='complete', terminal='no_next')
                break
            if page_number > 0 and not links:
                state.update(status='complete', terminal='empty_page')
                break
            if page_number + 1 >= max_pages:
                signal = f'safety_page_cap_reached:{max_pages}'
                signals.append(signal)
                state['terminal'] = signal
                break
            page_number += 1
            pages_attempted += 1
            next_url = (
                'https://www.ofim.fr/recherche.html?'
                + urlencode({'rp': 1, 'rt': 1, 'rc1': rc1_match.group(1), 'start': page_number * 10})
            )
            try:
                text, _ = fetch(next_url)
            except Exception:
                signals.append('fetch_failure')
                state['terminal'] = f'fetch_failure:page:{page_number + 1}'
                break
            pages_succeeded += 1
            if _blocked_or_challenge_page(text):
                signals.append('blocked_or_challenge_page')
                state['terminal'] = f'blocked_or_challenge_page:page:{page_number + 1}'
                break
            time.sleep(delay)
        route_states[slug] = state
    _set_snapshot_meta(
        'ofim', found=[(None, key, None) for key in all_unique],
        route_states=route_states, pages_attempted=pages_attempted,
        pages_succeeded=pages_succeeded, raw_items=raw_items, signals=signals,
        extra={
            'parsed_items': raw_items,
            'unique_ids': len(all_unique),
            'pre_unique_rejections_by_reason': (
                {'duplicate_raw': duplicate_raw} if duplicate_raw else {}
            ),
            'rejected_items_by_reason': (
                {'safety_item_cap': len(safety_rejected)} if safety_rejected else {}
            ),
        },
    )
    out = []
    rejected = {'out_of_scope': set(), 'detail_fetch_failure': set()}
    for url, ptype in found:
        match = re.search(r'ofim\.fr/(\d+)/', url)
        sid = match.group(1) if match else hashlib.md5(url.encode()).hexdigest()[:16]
        try:
            listing = detail_listing('ofim', url, ptype, sid)
        except Exception:
            rejected['detail_fetch_failure'].add(url)
            continue
        city = _target_city(listing.city)
        if not city:
            rejected['out_of_scope'].add(url)
            continue
        out.append(replace(listing, city=city))
        time.sleep(delay)
    _complete_adapter_rejections('ofim', rejected)
    if rejected['detail_fetch_failure']:
        _add_runtime_signal('ofim', 'detail_fetch_failure')
    return out


def _alter_catalogue_links(text):
    raw = re.findall(
        r'https:\\/\\/alter-immobilier\.re\\/post_type_annonces\\/[^"\\]+',
        text or '', re.I,
    )
    if not raw:
        raw = [
            'https://alter-immobilier.re' + value.replace('\\/', '/')
            for value in re.findall(r'\\/post_type_annonces\\/[^"\\]+', text or '', re.I)
        ]
    links = []
    for value in raw:
        url = value.replace('\\/', '/')
        if '/a-louer-' in url.lower() and url not in links:
            links.append(url)
    return links


def scrape_alter(max_items=5000):
    source = 'alter'
    url = 'https://alter-immobilier.re/nos-biens-a-louer/'
    try:
        text, _ = fetch(url)
    except Exception:
        _set_snapshot_meta(
            source, found=[], route_states={'catalogue': {
                'status': 'failed', 'terminal': 'fetch_failure', 'items': 0,
            }}, pages_attempted=1, pages_succeeded=0, raw_items=0,
            signals=['fetch_failure'], extra={'rejected_items_by_reason': {}},
        )
        raise
    links = _alter_catalogue_links(text)
    selected = links[:max_items]
    safety_rejected = set(links[max_items:])
    signals = []
    if safety_rejected:
        signals.append(f'safety_item_cap_reached:{max_items}')
    if _has_next_page(text, 2):
        signals.append('advertised_pagination_not_exhausted')
    if not links:
        signals.append('empty_catalogue_unproven')
    terminal = 'no_next' if not signals else signals[0]
    route_state = {
        'status': 'complete' if not signals else 'partial',
        'terminal': terminal, 'items': len(links),
    }
    _set_snapshot_meta(
        source, found=[(None, key, None) for key in links],
        route_states={'catalogue': route_state}, pages_attempted=1,
        pages_succeeded=1, raw_items=len(links), signals=signals,
        extra={'rejected_items_by_reason': (
            {'safety_item_cap': len(safety_rejected)} if safety_rejected else {}
        )},
    )
    out = []
    rejected = {'out_of_scope': set(), 'detail_fetch_failure': set()}
    for listing_url in selected:
        sid = listing_url.rstrip('/').split('/')[-1]
        ptype = 'house' if any(value in listing_url.lower() for value in ('villa', 'maison')) else 'flat'
        try:
            listing = detail_listing(source, listing_url, ptype, sid)
        except Exception:
            rejected['detail_fetch_failure'].add(listing_url)
            continue
        city = _target_city(listing.city or guess_city_from_text(listing_url))
        if not city:
            rejected['out_of_scope'].add(listing_url)
            continue
        out.append(replace(listing, city=city))
    _complete_adapter_rejections(source, rejected)
    if rejected['detail_fetch_failure']:
        _add_runtime_signal(source, 'detail_fetch_failure')
    return out


ADREZIO_COMMUNES = {'Saint-Denis': 'saint-denis', 'Sainte-Marie': 'sainte-marie'}


def _adrezio_cards(text):
    return [
        match.group(0) for match in re.finditer(
            r'<a\b[^>]*href=["\'](/annonces/[a-z0-9]+)["\'][\s\S]*?</a>',
            text or '', re.I,
        )
    ]


def _adrezio_card_id(card):
    match = re.search(r'href=["\']/annonces/([a-z0-9]+)', card or '', re.I)
    return match.group(1) if match else None


def scrape_adrezio(max_items=5000, max_pages=50, delay=0.5):
    routes = {
        f'{city}:{ptype}': f'{ADREZIO_BASE}/reunion/location/{slug}/{city_slug}'
        for city, city_slug in ADREZIO_COMMUNES.items()
        for slug, ptype in ADREZIO_TYPES.items()
    }
    found = _walk_target_routes(
        source='adrezio', routes=routes, max_pages=max_pages,
        max_items=max_items, delay=delay, page_url=_page_query,
        parse_items=_adrezio_cards, item_key=_adrezio_card_id,
    )
    out = []
    rejected = {'mapping_failure': set(), 'out_of_scope': set()}
    for route_key, sid, card in found:
        route_city, ptype = route_key.split(':', 1)
        listings = _adrezio_card_listings(card, ptype, route_city, set())
        if not listings:
            rejected['mapping_failure'].add(sid)
            continue
        listing = listings[0]
        city = _target_city(listing.city)
        if city != route_city:
            rejected['out_of_scope'].add(sid)
            continue
        out.append(replace(listing, city=city))
    _complete_adapter_rejections('adrezio', rejected)
    return out


def _domimmo_item_id(item):
    value = item.get('id') or item.get('reference')
    if value not in (None, ''):
        return str(value)
    return hashlib.md5(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()


DOMIMMO_RENTAL_CATEGORY = 6
DOMIMMO_RESIDENTIAL_TYPES = (1, 2)
DOMIMMO_PAGE_LIMIT = 500
DOMIMMO_MAX_PAGES_PER_TYPE = 20


def scrape_domimmo(max_items=5000):
    source = 'domimmo'
    max_items = max(1, int(max_items))
    page_limit = min(max_items, DOMIMMO_PAGE_LIMIT)
    items = []
    route_states = {}
    signals = []
    pages_attempted = 0
    pages_succeeded = 0
    raw_items = 0
    cap_reached = False

    for property_type in DOMIMMO_RESIDENTIAL_TYPES:
        route_key = f'type:{property_type}'
        state = {'status': 'partial', 'terminal': None, 'items': 0}
        route_ids = set()
        if cap_reached:
            state['terminal'] = f'safety_item_cap_reached:{max_items}'
            route_states[route_key] = state
            continue
        for page in range(1, DOMIMMO_MAX_PAGES_PER_TYPE + 1):
            url = 'https://www.keldom.com/api/domimmo/offers?' + urlencode({
                'id_di_ad_cat': DOMIMMO_RENTAL_CATEGORY,
                'id_di_ad_type': property_type,
                'limit': page_limit,
                'page': page,
            })
            pages_attempted += 1
            try:
                text, _ = fetch(url)
            except Exception:
                state['terminal'] = f'fetch_failure:page:{page}'
                route_states[route_key] = state
                _set_snapshot_meta(
                    source, found=[], route_states=route_states,
                    pages_attempted=pages_attempted,
                    pages_succeeded=pages_succeeded, raw_items=raw_items,
                    signals=[*signals, 'fetch_failure'],
                    extra={'rejected_items_by_reason': {}},
                )
                raise
            try:
                payload = json.loads(text)
                page_items = payload if isinstance(payload, list) else (
                    payload.get('items') or payload.get('data') or []
                )
                if not isinstance(page_items, list):
                    raise ValueError('Domimmo payload has no item list')
            except Exception:
                state['terminal'] = f'invalid_payload:page:{page}'
                route_states[route_key] = state
                _set_snapshot_meta(
                    source, found=[], route_states=route_states,
                    pages_attempted=pages_attempted,
                    pages_succeeded=pages_succeeded + 1, raw_items=raw_items,
                    signals=[*signals, 'invalid_payload'],
                    extra={'rejected_items_by_reason': {}},
                )
                raise
            pages_succeeded += 1
            raw_items += len(page_items)
            new_ids = 0
            for item in page_items:
                if not isinstance(item, dict):
                    continue
                sid = _domimmo_item_id(item)
                if sid not in route_ids:
                    route_ids.add(sid)
                    new_ids += 1
            items.extend(page_items)
            state['items'] = len(route_ids)
            if len(items) >= max_items:
                signal = f'safety_item_cap_reached:{max_items}'
                signals.append(signal)
                state['terminal'] = signal
                cap_reached = True
                break
            if len(page_items) < page_limit:
                state.update(
                    status='complete',
                    terminal='empty_page' if not page_items else 'short_page',
                )
                break
            if not new_ids:
                signals.append('pagination_stalled_at_limit')
                state['terminal'] = f'pagination_stalled_at_limit:page:{page}'
                break
            if page == DOMIMMO_MAX_PAGES_PER_TYPE:
                signal = f'safety_page_cap_reached:{DOMIMMO_MAX_PAGES_PER_TYPE}'
                signals.append(signal)
                state['terminal'] = signal
                break
        route_states[route_key] = state

    unique = {}
    duplicate_raw = 0
    non_object_items = 0
    for item in items:
        if not isinstance(item, dict):
            non_object_items += 1
            continue
        sid = _domimmo_item_id(item)
        if sid in unique:
            duplicate_raw += 1
        else:
            unique[sid] = item
    _set_snapshot_meta(
        source, found=[(None, sid, None) for sid in unique],
        route_states=route_states, pages_attempted=pages_attempted,
        pages_succeeded=pages_succeeded, raw_items=raw_items,
        signals=signals, extra={
            'parsed_items': len(items) - non_object_items,
            'unique_ids': len(unique),
            'unparsed_items_by_reason': (
                {'non_object_item': non_object_items} if non_object_items else {}
            ),
            'pre_unique_rejections_by_reason': (
                {'duplicate_raw': duplicate_raw} if duplicate_raw else {}
            ),
            'rejected_items_by_reason': {},
            'api_limit': page_limit,
        },
    )

    out = []
    rejected = {
        'not_rental': set(), 'out_of_scope': set(),
        'non_residential': set(), 'invalid_price': set(),
    }
    for sid, item in unique.items():
        transaction = item.get('id_di_ad_cat')
        title = clean(item.get('title'))
        description = clean(item.get('description'))
        hay = f'{title or ""} {description or ""}'.lower()
        if str(transaction) != '6':
            rejected['not_rental'].add(sid)
            continue
        city = _target_city(item.get('city'))
        if item.get('location') != 'REU' or not city:
            rejected['out_of_scope'].add(sid)
            continue
        if not is_domimmo_residential(title, description):
            rejected['non_residential'].add(sid)
            continue
        price = to_int_price(item.get('price'))
        if price is None or price < 250 or price > 6000:
            rejected['invalid_price'].add(sid)
            continue
        pieces = item.get('pieces')
        rooms = int(pieces) if isinstance(pieces, (int, float)) else parse_rooms(hay)
        bedrooms_raw = item.get('chambres')
        bedrooms = int(bedrooms_raw) if isinstance(bedrooms_raw, (int, float)) else None
        surface_raw = item.get('surface_habitable') or item.get('surface_terrain')
        try:
            surface = float(surface_raw) if surface_raw not in (None, '') else parse_surface(hay)
        except (TypeError, ValueError):
            surface = parse_surface(hay)
        photos = item.get('photos') if isinstance(item.get('photos'), list) else []
        image = item.get('imageSrc')
        if not image and photos:
            first = photos[0]
            image = first.get('src') if isinstance(first, dict) else str(first)
        type_id = str(item.get('id_di_ad_type') or '')
        ptype = 'house' if type_id == '2' or any(value in hay for value in ('maison', 'villa')) else 'flat'
        listing_url = f'https://www.domimmo.com/reunion/immobilier/{sid}/'
        data = {
            'api': 'keldom_domimmo_offers', 'url': listing_url,
            'title': title, 'city': item.get('city'), 'price': price,
            'surface_habitable': item.get('surface_habitable'),
            'pieces': pieces, 'chambres': bedrooms_raw,
            'publicationDate': item.get('publicationDate'), 'image': image,
            'photo_count': len(photos), 'description': description, 'raw': item,
        }
        out.append(Listing(
            source, sid, listing_url, listing_url, title, city, None, ptype,
            rooms, bedrooms, surface, price, to_int_price(item.get('charges')),
            item.get('company'), item.get('publicationDate'), image, description,
            save_raw(source, sid, data), hash_listing(data),
        ))
    _complete_adapter_rejections(source, rejected)
    return out
def init_db(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS rental_listings (
        source_site TEXT NOT NULL, source_id TEXT NOT NULL, url TEXT NOT NULL, canonical_url TEXT, title TEXT, city TEXT, district TEXT, property_type TEXT, rooms INTEGER, bedrooms INTEGER, surface_m2 REAL, rent_eur INTEGER, charges_eur INTEGER, agency_or_owner TEXT, published_at TEXT, seen_first_at TEXT NOT NULL, seen_last_at TEXT NOT NULL, image_url TEXT, description TEXT, raw_json_path TEXT, content_hash TEXT, is_active INTEGER DEFAULT 1, PRIMARY KEY(source_site, source_id))''')

def upsert(conn,l):
    now=datetime.now(timezone.utc).isoformat(); d=asdict(l)
    old=conn.execute('select content_hash,is_active from rental_listings where source_site=? and source_id=?',(l.source_site,l.source_id)).fetchone()
    if not old:
        conn.execute('INSERT INTO rental_listings (source_site,source_id,url,canonical_url,title,city,district,property_type,rooms,bedrooms,surface_m2,rent_eur,charges_eur,agency_or_owner,published_at,seen_first_at,seen_last_at,image_url,description,raw_json_path,content_hash,is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)',(d['source_site'],d['source_id'],d['url'],d['canonical_url'],d['title'],d['city'],d['district'],d['property_type'],d['rooms'],d['bedrooms'],d['surface_m2'],d['rent_eur'],d['charges_eur'],d['agency_or_owner'],d['published_at'],now,now,d['image_url'],d['description'],d['raw_json_path'],d['content_hash']))
        return 'new'
    status='reappeared' if not old[1] else ('changed' if old[0]!=l.content_hash else 'seen')
    conn.execute('UPDATE rental_listings SET url=?,canonical_url=?,title=?,city=?,district=?,property_type=?,rooms=?,bedrooms=?,surface_m2=?,rent_eur=?,charges_eur=?,agency_or_owner=?,published_at=COALESCE(?,published_at),seen_last_at=?,image_url=?,description=?,raw_json_path=?,content_hash=?,is_active=1 WHERE source_site=? AND source_id=?',(d['url'],d['canonical_url'],d['title'],d['city'],d['district'],d['property_type'],d['rooms'],d['bedrooms'],d['surface_m2'],d['rent_eur'],d['charges_eur'],d['agency_or_owner'],d['published_at'],now,d['image_url'],d['description'],d['raw_json_path'],d['content_hash'],d['source_site'],d['source_id']))
    return status


def build_source_manifest(*, source, run_id, listings, event_statuses,
                          source_status, fetch_log, runtime_meta=None):
    """Return exact counters and a conservative completeness classification."""
    runtime_meta = dict(runtime_meta or {})
    pages_attempted = int(runtime_meta.get('pages_attempted', len(fetch_log)) or 0)
    pages_succeeded = int(runtime_meta.get(
        'pages_succeeded', sum(1 for row in fetch_log if row.get('ok'))
    ) or 0)
    normalized = len(listings)
    fetched_items = int(runtime_meta.get('raw_items', normalized) or 0)
    parsed_items = int(runtime_meta.get('parsed_items', fetched_items) or 0)
    seen_ids = sorted({
        str(getattr(item, 'source_id', '')) for item in listings
        if getattr(item, 'source_id', None)
    })
    listing_unique = len(seen_ids)
    unique_ids = int(runtime_meta.get('unique_ids', listing_unique) or 0)

    def _reason_counts(value):
        valid = isinstance(value, dict)
        counts = {}
        if not valid:
            return counts, False
        for raw_reason, raw_count in value.items():
            reason = str(raw_reason).strip()
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                valid = False
                continue
            if not reason or count <= 0:
                valid = False
                continue
            counts[reason] = count
        return counts, valid

    unparsed_reasons, unparsed_valid = _reason_counts(
        runtime_meta.get('unparsed_items_by_reason') or {}
    )
    pre_unique_reasons, pre_unique_valid = _reason_counts(
        runtime_meta.get('pre_unique_rejections_by_reason') or {}
    )
    rejection_reasons, rejection_accounting_valid = _reason_counts(
        runtime_meta.get('rejected_items_by_reason') or {}
    )
    rejected_items = unique_ids - normalized
    rejection_accounting_valid = (
        rejection_accounting_valid
        and sum(rejection_reasons.values()) == rejected_items
    )
    stage_accounting_valid = (
        unparsed_valid and pre_unique_valid and rejection_accounting_valid
        and fetched_items >= parsed_items >= unique_ids >= normalized >= 0
        and sum(unparsed_reasons.values()) == fetched_items - parsed_items
        and sum(pre_unique_reasons.values()) == parsed_items - unique_ids
        and listing_unique == normalized
    )
    signals = [str(x) for x in runtime_meta.get('truncation_signals', []) if x]
    if source in BOUNDED_PARTIAL_SOURCES:
        signals.append('bounded_pagination')
    cap = SOURCE_RESULT_CAPS.get(source)
    if cap is not None and normalized >= cap:
        signals.append(f'result_cap_reached:{cap}')
    if not runtime_meta.get('full_snapshot_proof') and any(not row.get('ok') for row in fetch_log):
        signals.append('fetch_failure')
    if pages_attempted == 0:
        signals.append('no_page_evidence')
    if not runtime_meta.get('full_snapshot_proof'):
        signals.append('full_snapshot_unproven')
    unparsed_count = sum(unparsed_reasons.values())
    if unparsed_count > 0:
        signals.append(f'unparsed_items_present:{unparsed_count}')
    signals = list(dict.fromkeys(signals))
    if not rejection_accounting_valid:
        signals.append('rejection_accounting_mismatch')
    if not stage_accounting_valid:
        signals.append('stage_accounting_mismatch')

    error = source_status.get('error')
    full_snapshot_proof = bool(runtime_meta.get('full_snapshot_proof'))
    ok = (
        not error and (bool(source_status.get('ok')) or full_snapshot_proof)
        and (normalized > 0 or full_snapshot_proof)
    )
    if not ok:
        status = 'failed'
        error = str(error or f'{source} produced no usable listing')
    elif signals:
        status = 'partial'
        error = str(error) if error else None
    else:
        status = 'complete'
        error = None

    inserted = sum(value == 'new' for value in event_statuses)
    reappeared = sum(value == 'reappeared' for value in event_statuses)
    updated = sum(value in ('changed', 'reappeared') for value in event_statuses)
    unchanged = normalized - inserted - updated
    return {
        'run_id': str(run_id), 'source': str(source), 'status': status,
        'attempted': True,
        'pages_attempted': pages_attempted, 'pages_succeeded': pages_succeeded,
        'fetched_items': fetched_items, 'parsed_items': parsed_items,
        'unique_ids': unique_ids, 'normalized_items': normalized,
        'rejected_items': rejected_items,
        'rejected_items_by_reason': rejection_reasons,
        'unparsed_items_by_reason': unparsed_reasons,
        'pre_unique_rejections_by_reason': pre_unique_reasons,
        'inserted': inserted, 'updated': updated, 'unchanged': unchanged,
        'withdrawn': 0, 'reappeared': reappeared,
        'expected_count': runtime_meta.get('expected_count'),
        'previous_count': runtime_meta.get('previous_count'),
        'dataset_id': runtime_meta.get('dataset_id'),
        'retries': int(runtime_meta.get('retries', 0) or 0),
        'snapshot_proof': runtime_meta.get('snapshot_proof'),
        'seen_ids': seen_ids,
        'truncation_signals': signals, 'error': error,
    }


def main():
    global SCRAPLING_MODE, SCRAPLING_ENGINE
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='/opt/data/data/reunion_watch.db'); ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--only', '--sources', dest='only', default=None,
                    help='CSV de sources à exécuter (ex: leboncoin,adrezio). Défaut: toutes.')
    if _sf is not None:
        _sf.add_scrapling_args(ap)
    args=ap.parse_args()
    if _sf is not None:
        SCRAPLING_MODE=_sf.resolve_mode(getattr(args,'scrapling_mode','auto'))
        SCRAPLING_ENGINE=getattr(args,'scrapling_engine','http')
    funcs=[scrape_domimmo,scrape_locamoi,scrape_citya,scrape_zimo,scrape_immo974,scrape_fnaim,scrape_97immo,scrape_ofim,scrape_ofim_rss,scrape_alter,scrape_superimmo,scrape_leboncoin_apify_dataset,scrape_adrezio]
    if args.only:
        aliases = {
            'leboncoin': 'leboncoin_apify_dataset',
            'leboncoin_apify': 'leboncoin_apify_dataset',
        }
        by_name = {f.__name__.replace('scrape_', ''): f for f in funcs}
        wanted = []
        unknown = []
        for raw in args.only.split(','):
            name = raw.strip().lower()
            if not name:
                continue
            name = aliases.get(name, name)
            if name not in by_name:
                unknown.append(raw.strip())
                continue
            if name not in wanted:
                wanted.append(name)
        if unknown or not wanted:
            known = sorted(set(by_name) | set(aliases))
            ap.error(f"sources inconnues: {unknown or [args.only]}. Connues: {known}")
        funcs = [by_name[name] for name in wanted]
    events=[]; errors=[]
    conn=None
    if not args.dry_run:
        Path(args.db).parent.mkdir(parents=True,exist_ok=True); conn=sqlite3.connect(args.db); init_db(conn)
    source_status={}
    source_manifests={}
    run_id=os.environ.get('IMMO_RUN_ID') or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    try:
        for f in funcs:
            fname=f.__name__.replace('scrape_','')
            if fname == 'leboncoin_apify_dataset':
                fname = 'leboncoin'
            fetch_start=len(FETCH_LOG)
            SOURCE_RUNTIME_META.pop(fname, None)
            try:
                listings=f()
                empty_snapshot_proved = bool(
                    (SOURCE_RUNTIME_META.get(fname) or {}).get('full_snapshot_proof')
                )
                source_status[fname]={'ok': bool(listings) or empty_snapshot_proved, 'count': len(listings)}
                event_statuses=[]
                for l in listings:
                    status='dry' if args.dry_run else upsert(conn,l)
                    event_statuses.append(status)
                    event = {'status': status, **asdict(l)}
                    # Compatibility aliases for downstream/report consumers that
                    # use the public product vocabulary. Keep canonical DB fields
                    # rent_eur/surface_m2/image_url untouched.
                    if event.get('rent') is None:
                        event['rent'] = event.get('rent_eur')
                    if event.get('surface') is None:
                        event['surface'] = event.get('surface_m2')
                    if event.get('image') is None:
                        event['image'] = event.get('image_url')
                    if event.get('type') is None:
                        event['type'] = event.get('property_type')
                    events.append(event)
                # Commit after every source, not only at process end. The daily
                # cron has a hard timeout around this multi-source scraper; if a
                # later slow/anti-bot source times out, already refreshed sources
                # must still update seen_last_at so the freshness gate reflects
                # real progress instead of rolling back the whole batch.
                if conn:
                    conn.commit()
                source_manifests[fname]=build_source_manifest(
                    source=fname, run_id=run_id, listings=listings,
                    event_statuses=event_statuses, source_status=source_status[fname],
                    fetch_log=FETCH_LOG[fetch_start:], runtime_meta=SOURCE_RUNTIME_META.get(fname),
                )
            except Exception as e:
                source_status[fname]={'ok': False, 'count': 0, 'error': repr(e)}
                errors.append({'source':f.__name__,'error':repr(e)})
                source_manifests[fname]=build_source_manifest(
                    source=fname, run_id=run_id, listings=[], event_statuses=[],
                    source_status=source_status[fname], fetch_log=FETCH_LOG[fetch_start:],
                    runtime_meta=SOURCE_RUNTIME_META.get(fname),
                )
                if conn:
                    conn.commit()
    finally:
        if conn: conn.close()
    fetch_modes={}
    for row in FETCH_LOG:
        m=row.get('mode','?'); fetch_modes[m]=fetch_modes.get(m,0)+1
    scrapling_meta={'requested_mode':SCRAPLING_MODE,'engine':SCRAPLING_ENGINE,
                    'available':bool(_sf and _sf.SCRAPLING_AVAILABLE),
                    'probe':(_sf.SCRAPLING_INFO if _sf else {'available':False,'error':'helper_missing'}),
                    'fetch_count':len(FETCH_LOG),'fetch_by_mode':fetch_modes,
                    'errors_by_class':{}}
    for row in FETCH_LOG:
        ec=row.get('error_class') or 'none'
        if ec!='none':
            scrapling_meta['errors_by_class'][ec]=scrapling_meta['errors_by_class'].get(ec,0)+1
    summary={'events':len(events),'new':sum(e['status']=='new' for e in events),'changed':sum(e['status']=='changed' for e in events),'seen':sum(e['status']=='seen' for e in events),'by_source':{},'by_source_with_image':{},'source_status':source_status,'source_manifests':source_manifests,'errors':errors,'scrapling':scrapling_meta,'fetch_instrumentation':FETCH_LOG[:40],'sample':events[:20]}
    for e in events:
        src=e['source_site']
        summary['by_source'][src]=summary['by_source'].get(src,0)+1
        if e.get('image_url'):
            summary['by_source_with_image'][src]=summary['by_source_with_image'].get(src,0)+1
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
