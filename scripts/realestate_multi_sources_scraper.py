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
import argparse, hashlib, html, json, os, re, sqlite3, ssl, time
from dataclasses import dataclass, asdict, replace
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
    m=re.search(r'([0-9][0-9\s\u202f.,]*)',str(s))
    if not m: return None
    x=m.group(1).replace('\u202f',' ').replace(' ','').replace(',','.')
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

# Trouve le 27/07 (soir) : ne lisait que le POST initial (1 page, ~20
# annonces, toute l'ile, aucun cap avant). La pagination REELLE n'est pas
# un parametre 'page' (teste et infirme : memes IDs), mais offset/results
# en GET (ex. ?offset=20&results=20), verifie contre le lien natif
# "SUIVANT" du site (~20 pages, ~400 annonces au total). Pas de filtre
# commune a la source : le filtre reste client-side sur 'city'.
def scrape_immo974(max_pages=8, page_size=20, delay=1.5):
    arts=[]
    for page in range(max_pages):
        offset=page*page_size
        if offset==0:
            try:
                text,_=fetch('https://www.immo974.com/resultat-de-recherche',method='POST',data={'searchcategory':'2'})
            except Exception:
                break
        else:
            try:
                text,_=fetch(f'https://www.immo974.com/resultat-de-recherche?offset={offset}&results={page_size}')
            except Exception:
                break
        time.sleep(delay)
        page_arts=re.findall(r'<article>(.*?)</article>',text,re.I|re.S)
        if not page_arts:
            break
        arts.extend(page_arts)
    out=[]
    for a in arts:
        if '/annonce/locations/' not in a: continue
        url=re.search(r'href=["\']([^"\']*/annonce/locations/[^"\']+)["\']',a,re.I)
        if not url: continue
        url=html.unescape(url.group(1)); sid=re.search(r'-(\d+)\.html',url); sid=sid.group(1) if sid else hashlib.md5(url.encode()).hexdigest()
        title=re.search(r'<h2 class=["\']ville-type["\']>\s*<a[^>]*title=["\']([^"\']+)["\']',a,re.I|re.S)
        city=re.search(r'<h2 class=["\']localisation["\'][^>]*>.*?</i>\s*(.*?)\s*</h2>',a,re.I|re.S)
        price=re.search(r'<div class=["\']price-result["\'][^>]*>\s*<b>\s*([^<]+)',a,re.I|re.S)
        date=re.search(r'<div class=["\']date_publication["\'][^>]*>\s*([^<]+)',a,re.I|re.S)
        desc=re.search(r'<p class=["\']description["\'][^>]*>(.*?)</p>',a,re.I|re.S)
        img=re.search(r'<a[^>]+class=["\']img-list["\'][^>]*>\s*<img[^>]+src=["\']([^"\']+)',a,re.I|re.S)
        image_url=normalize_image_url(img.group(1), 'https://www.immo974.com/') if img else extract_image_url(a, 'https://www.immo974.com/')
        t=clean(title.group(1)) if title else None
        d={'url':url,'title':t,'city':clean(city.group(1)) if city else None,'price':clean(price.group(1)) if price else None,'date':clean(date.group(1)) if date else None,'desc':clean(desc.group(1)) if desc else None}
        out.append(Listing('immo974',sid,url,url,t,d['city'],None,'apartment' if 'appartement' in url else ('house' if 'maison' in url or 'villa' in url else None),parse_rooms((t or '')+' '+(d.get('desc') or '')),None,parse_surface((t or '')+' '+(d.get('desc') or '')),to_int_price(d['price']),None,None,d['date'],image_url,d['desc'],save_raw('immo974',sid,d),hash_listing(d)))
    return out

# Trouve le 27/07 (soir) : ne lisait que la page 1 (96 annonces, toute l'ile,
# pas de filtre commune cote zimo -- pas de page par commune comme citya).
# La pagination existe (?page=N, verifie jusqu'a la page 20 pleine, 0 vide a
# la page 25, aucun chevauchement d'ID entre pages) mais le volume total est
# tres grand (potentiellement 1900+ annonces toute l'ile). Prudence anti-
# bannissement : on ne prend que quelques pages de plus par run (delay entre
# pages), pas tout d'un coup -- la couverture Nord+Est se construira sur
# plusieurs jours, comme pour detail_enrich.py.
def scrape_zimo(max_pages=6, delay=2.0):
    arts=[]
    for page in range(1, max_pages+1):
        url='https://www.zimo.fr/annonces/location/la-reunion-974'
        if page>1:
            url+=f'?page={page}'
        try:
            text,_=fetch(url)
        except Exception:
            break
        page_arts=re.findall(r'<article\b[^>]*>(.*?)</article>',text,re.I|re.S)
        if not page_arts:
            break
        arts.extend(page_arts)
        time.sleep(delay)
    out=[]
    for a in arts:
        m=re.search(r'<a href=["\'](/annonce/[^"\']+)["\'][^>]*title=["\']([^"\']+)',a,re.I|re.S)
        if not m: continue
        url='https://www.zimo.fr'+html.unescape(m.group(1)); sid=url.rstrip('/').split('/')[-1]
        price=re.search(r'<span class=["\']badge ink base["\']>\s*([^<]*€)',a,re.I|re.S)
        info=re.search(r'<div class=["\'][^"\']*font-medium[^"\']*["\']>\s*(.*?)\s*</div>',a,re.I|re.S)
        source=re.search(r'<span>([^<]+)</span>\s*<i class=["\']fas fa-caret-right',a,re.I|re.S)
        tim=re.search(r'<time>(.*?)</time>',a,re.I|re.S)
        title=clean(m.group(2)); inf=clean(info.group(1)) if info else title
        city=None; cm=re.search(r'Location\s+(?:T\d\s+)?([^()]+?)\s*\(974\)',inf or '',re.I)
        if cm:
            city=clean(cm.group(1))
            # Zimo often prefixes the location with the property subtype:
            # "Studio Saint-Denis", "Duplex Le Tampon", "Maison individuelle Saint-Paul".
            city=re.sub(r'^(studio|duplex|appartement|maison individuelle|maison|villa|box|bureaux?|local commercial)\s+', '', city or '', flags=re.I).strip() or city
        d={'url':url,'title':title,'info':inf,'price':clean(price.group(1)) if price else None,'source':clean(source.group(1)) if source else None,'time':clean(tim.group(1)) if tim else None, 'image': extract_image_url(a, 'https://www.zimo.fr/')}
        ptype='commercial' if any(x in (inf or '').lower() for x in ['bureau','bureaux','local commercial','commerce']) else ('box' if any(x in (inf or '').lower() for x in ['box','garde meuble']) else ('house' if 'maison' in (inf or '').lower() else ('flat' if any(x in (inf or '').lower() for x in ['appartement','studio','duplex','t1','t2','t3','t4']) else None)))
        out.append(Listing('zimo',sid,url,url,title,city,None,ptype,parse_rooms(inf),None,parse_surface(inf),to_int_price(d['price']),None,d['source'],d['time'],d['image'],inf,save_raw('zimo',sid,d),hash_listing(d)))
    return out

def scrape_superimmo():
    text,_=fetch('https://www.superimmo.com/location/dom-tom/la-reunion')
    arts=re.findall(r'<article\b[^>]*data-public-id=["\']([^"\']+)["\'][^>]*>(.*?)</article>',text,re.I|re.S)
    out=[]
    for sid,a in arts:
        u=re.search(r'data-url-with-next-prev=["\']([^"\']+)',a,re.I) or re.search(r'data-js-url=["\']([^"\']*/annonces/[^"\']+)',a,re.I)
        url=urljoin('https://www.superimmo.com',html.unescape(u.group(1))) if u else f'https://www.superimmo.com/annonces/{sid}'
        txt=clean(a) or ''
        # Superimmo can glue date+price; use CC marker when possible
        pm=re.search(r'([0-9]{2,4})\s*€\s*CC',txt)
        price=to_int_price(pm.group(1)) if pm else None
        # title from URL fallback
        title=clean(re.sub(r'-x[0-9a-z]+.*','',url.split('/annonces/')[-1]).replace('-',' ')) if '/annonces/' in url else txt[:120]
        cm=re.search(r'(saint[- ]denis|sainte[- ]clotilde|le[- ]tampon|saint[- ]pierre|saint[- ]paul|la[- ]possession|les[- ]avvirons|sainte[- ]marie|saint[- ]leu)',url,re.I)
        city=clean(cm.group(1).replace('-',' ')) if cm else None
        d={'url':url,'title':title,'text':txt[:500],'price':price,'image': extract_image_url(a, 'https://www.superimmo.com/')}
        out.append(Listing('superimmo',sid,url,url,title,city,None,'flat' if 'appartement' in url else ('house' if 'maison' in url else None),parse_rooms(txt),None,parse_surface(txt),price,None,None,None,d['image'],txt[:500],save_raw('superimmo',sid,d),hash_listing(d)))
    return out

# Trouve le 27/07 (soir) : ne lisait que la page 1 (meme defaut que citya/
# zimo/fnaim). Le site annonce "131 appartements" toute l'ile des la
# meta-description ; pagination confirmee via ?page=N (verifie jusqu'a la
# page 3, aucune page par commune -- filtre reste client-side sur 'city').
def scrape_locamoi(max_pages=7, delay=1.5):
    items=[]
    seen_urls=set()
    for page in range(1, max_pages+1):
        url='https://locamoi.fr/location/appartement/la-reunion'
        if page>1:
            url+=f'?page={page}'
        try:
            text,_=fetch(url)
        except Exception:
            break
        time.sleep(delay)
        m=re.search(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',text,re.I|re.S)
        if not m:
            break
        try:
            obj=json.loads(m.group(1))
        except Exception:
            break
        page_items=obj.get('mainEntity',{}).get('itemListElement',[])
        new=[it for it in page_items
             if (it.get('item',{}).get('url') or it.get('item',{}).get('offers',{}).get('url')) not in seen_urls]
        if not new:
            break
        for it in new:
            u=it.get('item',{}).get('url') or it.get('item',{}).get('offers',{}).get('url')
            if u:
                seen_urls.add(u)
            items.append(it)
    out=[]
    for it in items:
        item=it.get('item',{}); offers=item.get('offers',{}); offered=offers.get('itemOffered',{})
        url=item.get('url') or offers.get('url'); sid=url.rstrip('/').split('-')[-1] if url else str(it.get('position'))
        addr=offered.get('address',{}) if isinstance(offered.get('address'),dict) else {}
        title=clean(item.get('name')); price=offers.get('price')
        surf=offered.get('floorSize',{}).get('value') if isinstance(offered.get('floorSize'),dict) else None
        rooms=offered.get('numberOfBedrooms',{}).get('value') if isinstance(offered.get('numberOfBedrooms'),dict) else None
        d={'url':url,'title':title,'price':price,'city':addr.get('addressLocality'),'surface':surf,'rooms':rooms,'image':item.get('image')}
        out.append(Listing('locamoi',sid,url,url,title,addr.get('addressLocality'),None,'flat',int(rooms) if isinstance(rooms,(int,float)) else None,None,float(surf) if isinstance(surf,(int,float)) else None,int(price) if isinstance(price,(int,float)) else to_int_price(price),None,'locamoi/aggregated',offers.get('validFrom'),item.get('image'),title,save_raw('locamoi',sid,d),hash_listing(d)))
    return out

# Trouve le 28/07 : `[^"\']+` s'arrete a la PREMIERE apostrophe rencontree
# dans le contenu, meme quand l'attribut est delimite par des guillemets
# doubles -- coupe "Immobilier La Reunion L'..." et "à louer à l'..." net a
# l'apostrophe. Preuve : 97immo et ofim en sont pleins (texte francais =
# apostrophes partout). Corrige en capturant jusqu'a la MEME quote que
# celle qui a ouvert l'attribut (backreference), pas n'importe laquelle.
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

# Trouve le 27/07 (soir) : ne lisait que /locations/1 (meme defaut que
# citya/zimo). Le site paginate reellement sur /locations/N (verifie
# jusqu'a la page 12 pleine), pas de filtre commune a la source -- la
# ville est deja encodee dans le slug d'URL (ex. "...-st-denis-3-pieces-
# ..."), exploitable par city_from_url() deja existant dans detail_listing.
def scrape_fnaim(max_items=90, max_pages=12, delay=1.5):
    links=[]
    seen=set()
    for page in range(1, max_pages+1):
        try:
            text,_=fetch(f'https://www.fnaim.re/locations/{page}')
        except Exception:
            break
        time.sleep(delay)
        page_links=unique_links(text,r'href=["\']([^"\']*id-location-[^"\']+)["\']','https://www.fnaim.re/',max_items)
        new=[u for u in page_links if u not in seen]
        if not new:
            break
        for u in new:
            seen.add(u)
            links.append(u)
        if len(links) >= max_items:
            break
    out=[]
    for u in links[:max_items]:
        sid=re.search(r'id-location-[^/]+-(\d+)/?',u)
        try: out.append(detail_listing('fnaim',u,'house' if 'maison' in u else 'flat',sid.group(1) if sid else None))
        except Exception: pass
        time.sleep(delay)
    return out

# Trouve le 27/07 (soir) : ne lisait que la page toute-l'ile (meme defaut
# que citya) et ratait structurellement Sainte-Marie/Sainte-Suzanne/
# Saint-Andre. Le site a un vrai moteur de recherche par commune
# (immo_liste_location.php?id_localisations[]=<ID>) qui paginate (&page=N,
# verifie : 0 chevauchement entre page 1 et 2). IDs communes verifies :
# Saint-Denis=195, Sainte-Marie=82, Sainte-Suzanne=131, Saint-Andre=220.
IMMO97_COMMUNES = {
    'Saint-Denis': '195', 'Sainte-Marie': '82', 'Sainte-Suzanne': '131', 'Saint-André': '220',
}


def scrape_97immo(max_items=90, max_pages=4, delay=1.5):
    found=[]  # (url, commune)
    seen=set()
    for commune, cid in IMMO97_COMMUNES.items():
        for page in range(1, max_pages+1):
            url=(f'https://www.97immo.com/immo_liste_location.php?id_typeoffres=location'
                 f'&id_destinations=19&id_localisations%5B%5D={cid}&typelien=moteur_search')
            if page>1:
                url+=f'&page={page}'
            try:
                text,_=fetch(url)
            except Exception:
                break
            time.sleep(delay)
            # Trouve le 27/07 (soir) : le motif 'maison-villa' ratait les
            # vraies URLs '/location/maison/...' (verifie sur Sainte-Suzanne :
            # les 2 seules annonces residentielles reelles utilisent ce
            # segment sans '-villa', 100% ratees avant ce correctif).
            links=unique_links(text,r'href=["\']([^"\']*/immobilier-annonce/location/(?:appartement|maison(?:-villa)?)[^"\']+)["\']','https://www.97immo.com/',50)
            new=[u for u in links if u not in seen]
            if not new:
                break
            for u in new:
                seen.add(u)
                found.append((u, commune))
            if len(found) >= max_items:
                break
        if len(found) >= max_items:
            break
    out=[]
    for u, commune in found[:max_items]:
        sid=u.rstrip('/').split('/')[-2] + '_' + u.rstrip('/').split('/')[-1]
        try:
            l=detail_listing('97immo',u,'house' if '/maison' in u else 'flat',sid)
            out.append(replace(l, city=commune))
        except Exception: pass
        time.sleep(delay)
    return out

# Trouve le 27/07 (soir) : la source "ofim" (via ofim_rental_scraper.py,
# hors depot) et "ofim_rss" (ci-dessous) scrapaient TOUTES LES DEUX le meme
# flux RSS identique (rss.php == rss.xml, verifie octet pres), plafonne a
# 50 par ofim.fr lui-meme -- double travail pour zero gain. Le vrai
# catalogue (113 biens) vit sur des pages de categorie HTML STATIQUES
# (liste-location-appartements.html, liste-location-villas.html...),
# PAS besoin de navigateur (verifie : cartes avec data-annonce-id et lien
# reel deja dans le HTML brut d'une requete urllib simple). Pagination
# reelle via recherche.html?rc1=<N>&start=<offset>, rc1 decouvert sur la
# page 1 de chaque categorie plutot que devine. Limite aux 2 categories
# residentielles (appartements/villas = 65/113 biens) : le reste (terrains,
# bureaux, locaux, entrepots) est hors perimetre du projet (veille locative
# residentielle Nord+Est), pas verifie faute d'interet.
OFIM_CATEGORIES = {'appartements': 'flat', 'villas': 'house'}


def scrape_ofim(max_items=90, max_pages=6, delay=1.5):
    found=[]  # (url, ptype)
    seen=set()
    for slug, ptype in OFIM_CATEGORIES.items():
        try:
            text,_=fetch(f'https://www.ofim.fr/liste-location-{slug}.html')
        except Exception:
            continue
        time.sleep(delay)
        rc1_m=re.search(r'rc1=(\d+)',text)
        links=unique_links(text,r'href=["\'](https://www\.ofim\.fr/\d+/Location-[^"\']+)["\']','https://www.ofim.fr/',50)
        for u in links:
            if u not in seen:
                seen.add(u); found.append((u,ptype))
        if rc1_m:
            rc1=rc1_m.group(1)
            for page in range(1,max_pages):
                start=page*10
                try:
                    text,_=fetch(f'https://www.ofim.fr/recherche.html?rp=1&rt=1&rc1={rc1}&start={start}')
                except Exception:
                    break
                time.sleep(delay)
                links=unique_links(text,r'href=["\'](https://www\.ofim\.fr/\d+/Location-[^"\']+)["\']','https://www.ofim.fr/',50)
                # OFIM can reorder/overlap list windows between the category
                # seed page and search.html?start=N. A page with only already
                # seen URLs is not catalogue end; only a truly empty page is.
                # Otherwise the scrape becomes order-dependent and listings
                # disappear/reappear on the next pass despite stable OFIM IDs.
                if not links:
                    break
                for u in links:
                    if u not in seen:
                        seen.add(u); found.append((u,ptype))
                if len(found)>=max_items:
                    break
        if len(found)>=max_items:
            break
    out=[]
    for u,ptype in found[:max_items]:
        sid_m=re.search(r'ofim\.fr/(\d+)/',u)
        sid=sid_m.group(1) if sid_m else hashlib.md5(u.encode()).hexdigest()[:16]
        try: out.append(detail_listing('ofim',u,ptype,sid))
        except Exception: pass
        time.sleep(delay)
    return out


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

def scrape_alter(max_items=25):
    text,_=fetch('https://alter-immobilier.re/nos-biens-a-louer/')
    # Alter renders listings from escaped JSON; hrefs are not plain anchors.
    raw=re.findall(r'https:\\/\\/alter-immobilier\.re\\/post_type_annonces\\/[^"\\]+', text)
    raw += ['https://alter-immobilier.re'+u.replace('\\/','/') for u in re.findall(r'\\/post_type_annonces\\/[^"\\]+', text)]
    links=[]
    for u in raw:
        u=u.replace('\\/','/')
        if '/a-louer-' not in u.lower():
            continue
        if u not in links:
            links.append(u)
        if len(links)>=max_items: break
    out=[]
    for u in links:
        sid=u.rstrip('/').split('/')[-1]
        try: out.append(detail_listing('alter',u,'house' if any(x in u.lower() for x in ['villa','maison']) else 'flat',sid))
        except Exception: pass
    return out

# Trouve le 27/07 (soir) : l'ancienne version ne lisait que la page toute-l'ile
# (la-reunion-974), qui est surtout un ANNUAIRE de liens vers les pages par
# commune -- la plupart des 50 liens captes (plafond max_items*2) n'etaient
# meme pas des annonces. Preuve : Saint-Denis seul a 34 appartements reels
# chez citya (2 pages), alors que toute la base ne comptait que 2 annonces
# citya actives, toutes communes confondues. Les pages par commune existent
# deja cote citya (memes slugs que nos 4 communes cibles) et paginent
# (?page=2). Les cartes sont bien dans le HTML statique (data-itemId="GES..."
# sur un <div>, pas un <a href> -- l'URL se reconstruit : recherche +
# "/" + itemId, verifie sur un cas reel) : pas besoin de navigateur.
CITYA_COMMUNES = {
    'Saint-Denis': 'saint-denis-97411',
    'Sainte-Marie': 'sainte-marie-97438',
    'Sainte-Suzanne': 'sainte-suzanne-97441',
    'Saint-André': 'saint-andre-97440',
}


def scrape_citya(max_items=90, max_pages=4, delay=1.5):
    found=[]  # (item_id, ptype, url, commune)
    seen_ids=set()
    for commune, slug in CITYA_COMMUNES.items():
        for ptype in ('appartement', 'maison'):
            for page in range(1, max_pages+1):
                url = f'https://www.citya.com/annonces/location/{ptype}/{slug}'
                if page > 1:
                    url += f'?page={page}'
                try:
                    text,_=fetch(url)
                except Exception:
                    break
                time.sleep(delay)
                ids=[m for m in re.findall(r'data-itemId=["\']([A-Z0-9-]+)["\']', text) if m not in seen_ids]
                if not ids:
                    break
                for item_id in ids:
                    seen_ids.add(item_id)
                    found.append((item_id, ptype, f'https://www.citya.com/annonces/location/{ptype}/{slug}/{item_id}', commune))
                if len(found) >= max_items:
                    break
            if len(found) >= max_items:
                break
        if len(found) >= max_items:
            break
    out=[]
    for item_id, ptype, u, commune in found[:max_items]:
        try:
            l=detail_listing('citya',u,'house' if ptype=='maison' else 'flat',item_id)
            # La commune interrogee est le signal SUR, contrairement au
            # devinage depuis le texte de detail_listing() (trouve fautif :
            # "Le Tampon"/"Saint-Pierre"/etc. sur des pages citya-Saint-Denis,
            # probablement le texte d'agence qui mentionne d'autres secteurs).
            out.append(replace(l, city=commune))
        except Exception: pass
        time.sleep(delay)
    return out

# Trouve le 27/07 (soir) : `offset` est ignore par l'API Keldom (teste :
# offset=200 renvoie le meme set que limit=200 sans offset -- ce n'est pas
# une vraie pagination page/offset). Le vrai levier est `limit` seul :
# 200->500 triple le nombre d'offres Reunion utiles apres filtre. Au-dela
# de ~700-1000, l'API renvoie 500 (verifie : 1000 et 3000 echouent).
def scrape_domimmo(max_items=150):
    """Domimmo now serves its usable data through Keldom's JSON API.

    The legacy domimmo.com list page has become empty/fragile. Keldom's API is
    public and returns Domimmo-partner offers across DOM territories, so we fetch
    a broad slice then keep only Réunion rental-shaped offers. This keeps the
    source fresh without changing downstream filtering semantics.
    """
    params={'limit':'500'}
    text,_=fetch('https://www.keldom.com/api/domimmo/offers?'+urlencode(params))
    payload=json.loads(text)
    items=payload if isinstance(payload,list) else (payload.get('items') or payload.get('data') or [])
    out=[]
    for item in items:
        if not isinstance(item,dict):
            continue
        title=clean(item.get('title'))
        desc=clean(item.get('description'))
        hay=((title or '')+' '+(desc or '')).lower()
        price=to_int_price(item.get('price'))
        if item.get('location') != 'REU':
            continue
        if price is None or price < 250 or price > 6000:
            continue
        if not any(tok in hay for tok in ['location','louer','loyer','à louer','a louer']):
            continue
        sid=str(item.get('id') or item.get('reference') or hashlib.md5(json.dumps(item,sort_keys=True,default=str).encode()).hexdigest())
        url=f'https://www.domimmo.com/reunion/immobilier/{sid}/'
        city=clean(item.get('city'))
        pieces=item.get('pieces')
        rooms=int(pieces) if isinstance(pieces,(int,float)) else parse_rooms((title or '')+' '+(desc or ''))
        chambres=item.get('chambres')
        bedrooms=int(chambres) if isinstance(chambres,(int,float)) else None
        surf=item.get('surface_habitable') or item.get('surface_terrain')
        try:
            surface=float(surf) if surf not in (None,'') else parse_surface((title or '')+' '+(desc or ''))
        except Exception:
            surface=parse_surface((title or '')+' '+(desc or ''))
        image=item.get('imageSrc')
        photos=item.get('photos') if isinstance(item.get('photos'),list) else []
        if not image and photos:
            first=photos[0]
            image=first.get('src') if isinstance(first,dict) else str(first)
        ptype='house' if any(x in hay for x in ['maison','villa']) else ('flat' if any(x in hay for x in ['appartement','studio','t1','t2','t3','t4','t5']) else None)
        d={'api':'keldom_domimmo_offers','url':url,'title':title,'city':city,'price':price,'surface_habitable':item.get('surface_habitable'),'pieces':pieces,'chambres':chambres,'publicationDate':item.get('publicationDate'),'image':image,'photo_count':len(photos),'description':desc,'raw':item}
        out.append(Listing('domimmo',sid,url,url,title,city,None,ptype,rooms,bedrooms,surface,price,to_int_price(item.get('charges')),item.get('company'),item.get('publicationDate'),image,desc,save_raw('domimmo',sid,d),hash_listing(d)))
        if len(out)>=max_items:
            break
    return out


# --- Leboncoin via Apify actor piotrv1001/leboncoin-listings-scraper ---------
# Leboncoin blocks plain scraping, so we go through the Apify actor. The actor's
# default dataset (native leboncoin ad objects) is what we map here. Two modes:
#   * APIFY_LEBONCOIN_DATASET_ID set  -> read that existing dataset, no actor run
#     (cheap, reproducible: reuse a run already paid for).
#   * otherwise -> run the actor synchronously and read its default dataset.
# The Apify token (APIFY_TOKEN) is sent ONLY in the Authorization header, never
# in a URL nor in FETCH_LOG -- so it cannot leak into logs/summary output.
APIFY_BASE = 'https://api.apify.com/v2'
DEFAULT_LEBONCOIN_ACTOR = 'piotrv1001~leboncoin-listings-scraper'
# 4 communes cibles (Nord+Est), memes que citya/97immo. (commune, code postal).
LEBONCOIN_COMMUNES = [
    ('Saint-Denis', '97400'), ('Sainte-Marie', '97438'),
    ('Sainte-Suzanne', '97441'), ('Saint-André', '97440'),
]
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


def _map_leboncoin_item(item):
    """Map one Apify leboncoin dataset item to a Listing, or None when the ad is
    not a residential rental (keeps land/parking/commercial out). Handles both
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
    for item in items or []:
        listing = _map_leboncoin_item(item)
        if listing is not None:
            out.append(listing)
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
    return {
        # Actor piotrv1001 expects Leboncoin location slugs as strings
        # (validated in the PC handoff), not our internal commune dicts.
        'locations': [f'{c}_{z}' for c, z in LEBONCOIN_COMMUNES],
        'categoryIds': ['10'],
        'maxItems': max_items,
        'includeDetails': True,
        'sort': 'time',
        'proxyConfiguration': {
            'useApifyProxy': True,
            'apifyProxyGroups': ['RESIDENTIAL'],
            'apifyProxyCountry': 'FR',
        },
    }


def scrape_leboncoin_apify_dataset():
    """Leboncoin residential rentals via Apify (piotrv1001/leboncoin-listings-scraper).

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
        max_items = int(os.environ.get('APIFY_LEBONCOIN_MAX_ITEMS', '') or 40)
    except ValueError:
        max_items = 40
    if not token:
        raise RuntimeError('APIFY_TOKEN missing: cannot query Apify (leboncoin)')
    if dataset_id:
        url = (f'{APIFY_BASE}/datasets/{quote(dataset_id, safe="")}/items'
               f'?clean=true&format=json&limit={max_items}')
        items = _apify_json(url, token)
        run = None
        mode = 'dataset'
        actor = os.environ.get('APIFY_LEBONCOIN_ACTOR', '').strip() or DEFAULT_LEBONCOIN_ACTOR
    else:
        actor = os.environ.get('APIFY_LEBONCOIN_ACTOR', '').strip() or DEFAULT_LEBONCOIN_ACTOR
        run_url = (f'{APIFY_BASE}/acts/{quote(actor, safe="~")}/runs'
                   f'?waitForFinish=180')
        run = _apify_json(run_url, token, payload=_leboncoin_actor_input(max_items))
        dataset_id = run.get('defaultDatasetId') if isinstance(run, dict) else None
        if not dataset_id:
            raise RuntimeError('Apify run finished without defaultDatasetId')
        url = (f'{APIFY_BASE}/datasets/{quote(dataset_id, safe="")}/items'
               f'?clean=true&format=json&limit={max_items}')
        items = _apify_json(url, token)
        mode = 'actor_run'
    if isinstance(items, dict):
        items = items.get('items') or items.get('data') or []
    items = items if isinstance(items, list) else []
    _write_apify_usage(mode=mode, actor=actor, dataset_id=dataset_id,
                       run=run, result_count=len(items))
    return _leboncoin_listings(items)


# --- Adrezio (agence Reunion) : pages liste statiques, fetch() HTTP simple -----
# Trouve : Adrezio publie ses locations sur des pages de liste par commune et par
# type, /reunion/location/{appartement,maison}/{commune-slug}?page=N. Le HTML est
# statique (pas de rendu JS) -> fetch() urllib suffit, PAS de Playwright/CDP. La
# commune interrogee est le signal SUR (comme citya/97immo), on l'impose sur le
# resultat plutot que de la deviner depuis le texte de detail.
ADREZIO_BASE = 'https://adrezio.fr'
ADREZIO_COMMUNES = {
    'Saint-Denis': 'saint-denis', 'Sainte-Marie': 'sainte-marie',
    'Sainte-Suzanne': 'sainte-suzanne', 'Saint-André': 'saint-andre',
}
ADREZIO_TYPES = {'appartement': 'flat', 'maison': 'house'}


def scrape_adrezio(max_items=90, max_pages=4, delay=1.5):
    found = []  # (url, ptype, commune)
    seen = set()
    commune_slugs = set(ADREZIO_COMMUNES.values())
    for commune, slug in ADREZIO_COMMUNES.items():
        for tslug, ptype in ADREZIO_TYPES.items():
            for page in range(1, max_pages + 1):
                url = f'{ADREZIO_BASE}/reunion/location/{tslug}/{slug}'
                if page > 1:
                    url += f'?page={page}'
                try:
                    text, _ = fetch(url)
                except Exception:
                    break
                time.sleep(delay)
                # Listing cards link to stable detail URLs /annonces/<property_id>.
                links = unique_links(
                    text,
                    r'href=["\'](/annonces/[a-z0-9]+)["\']',
                    ADREZIO_BASE, 50)
                new = [u for u in links if u not in seen]
                if not new:
                    break
                for u in new:
                    seen.add(u)
                    found.append((u, ptype, commune))
                if len(found) >= max_items:
                    break
            if len(found) >= max_items:
                break
        if len(found) >= max_items:
            break
    out = []
    for u, ptype, commune in found[:max_items]:
        sid = u.rstrip('/').split('/')[-1]
        try:
            listing = detail_listing('adrezio', u, ptype, sid)
            out.append(replace(listing, city=commune))
        except Exception:
            pass
        time.sleep(delay)
    return out


def init_db(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS rental_listings (
        source_site TEXT NOT NULL, source_id TEXT NOT NULL, url TEXT NOT NULL, canonical_url TEXT, title TEXT, city TEXT, district TEXT, property_type TEXT, rooms INTEGER, bedrooms INTEGER, surface_m2 REAL, rent_eur INTEGER, charges_eur INTEGER, agency_or_owner TEXT, published_at TEXT, seen_first_at TEXT NOT NULL, seen_last_at TEXT NOT NULL, image_url TEXT, description TEXT, raw_json_path TEXT, content_hash TEXT, is_active INTEGER DEFAULT 1, PRIMARY KEY(source_site, source_id))''')

def upsert(conn,l):
    now=datetime.now(timezone.utc).isoformat(); d=asdict(l)
    old=conn.execute('select content_hash from rental_listings where source_site=? and source_id=?',(l.source_site,l.source_id)).fetchone()
    if not old:
        conn.execute('INSERT INTO rental_listings (source_site,source_id,url,canonical_url,title,city,district,property_type,rooms,bedrooms,surface_m2,rent_eur,charges_eur,agency_or_owner,published_at,seen_first_at,seen_last_at,image_url,description,raw_json_path,content_hash,is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)',(d['source_site'],d['source_id'],d['url'],d['canonical_url'],d['title'],d['city'],d['district'],d['property_type'],d['rooms'],d['bedrooms'],d['surface_m2'],d['rent_eur'],d['charges_eur'],d['agency_or_owner'],d['published_at'],now,now,d['image_url'],d['description'],d['raw_json_path'],d['content_hash']))
        return 'new'
    status='changed' if old[0]!=l.content_hash else 'seen'
    conn.execute('UPDATE rental_listings SET url=?,canonical_url=?,title=?,city=?,district=?,property_type=?,rooms=?,bedrooms=?,surface_m2=?,rent_eur=?,charges_eur=?,agency_or_owner=?,published_at=?,seen_last_at=?,image_url=?,description=?,raw_json_path=?,content_hash=?,is_active=1 WHERE source_site=? AND source_id=?',(d['url'],d['canonical_url'],d['title'],d['city'],d['district'],d['property_type'],d['rooms'],d['bedrooms'],d['surface_m2'],d['rent_eur'],d['charges_eur'],d['agency_or_owner'],d['published_at'],now,d['image_url'],d['description'],d['raw_json_path'],d['content_hash'],d['source_site'],d['source_id']))
    return status

def main():
    global SCRAPLING_MODE, SCRAPLING_ENGINE
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='/opt/data/data/reunion_watch.db'); ap.add_argument('--dry-run',action='store_true')
    if _sf is not None:
        _sf.add_scrapling_args(ap)
    args=ap.parse_args()
    if _sf is not None:
        SCRAPLING_MODE=_sf.resolve_mode(getattr(args,'scrapling_mode','auto'))
        SCRAPLING_ENGINE=getattr(args,'scrapling_engine','http')
    funcs=[scrape_domimmo,scrape_locamoi,scrape_citya,scrape_zimo,scrape_immo974,scrape_fnaim,scrape_97immo,scrape_ofim,scrape_ofim_rss,scrape_alter,scrape_superimmo,scrape_leboncoin_apify_dataset,scrape_adrezio]
    events=[]; errors=[]
    conn=None
    if not args.dry_run:
        Path(args.db).parent.mkdir(parents=True,exist_ok=True); conn=sqlite3.connect(args.db); init_db(conn)
    source_status={}
    try:
        for f in funcs:
            fname=f.__name__.replace('scrape_','')
            try:
                listings=f()
                source_status[fname]={'ok': True, 'count': len(listings)}
                for l in listings:
                    status='dry' if args.dry_run else upsert(conn,l)
                    events.append({'status':status, **asdict(l)})
                # Commit after every source, not only at process end. The daily
                # cron has a hard timeout around this multi-source scraper; if a
                # later slow/anti-bot source times out, already refreshed sources
                # must still update seen_last_at so the freshness gate reflects
                # real progress instead of rolling back the whole batch.
                if conn:
                    conn.commit()
            except Exception as e:
                source_status[fname]={'ok': False, 'count': 0, 'error': repr(e)}
                errors.append({'source':f.__name__,'error':repr(e)})
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
    summary={'events':len(events),'new':sum(e['status']=='new' for e in events),'changed':sum(e['status']=='changed' for e in events),'seen':sum(e['status']=='seen' for e in events),'by_source':{},'by_source_with_image':{},'source_status':source_status,'errors':errors,'scrapling':scrapling_meta,'fetch_instrumentation':FETCH_LOG[:40],'sample':events[:20]}
    for e in events:
        src=e['source_site']
        summary['by_source'][src]=summary['by_source'].get(src,0)+1
        if e.get('image_url'):
            summary['by_source_with_image'][src]=summary['by_source_with_image'].get(src,0)+1
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
