#!/usr/bin/env python3
"""Public data-truth audit: rendered cards/modals must match listings.json."""
from __future__ import annotations
import json, os, re, unicodedata
from typing import Any
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

BASE=os.environ.get('IMMO_PUBLIC_BASE','https://immo.148.230.103.174.sslip.io').rstrip('/')

def norm(s:Any)->str:
    return re.sub(r'\s+',' ',''.join(c for c in unicodedata.normalize('NFD',str(s or '').lower()) if unicodedata.category(c)!='Mn')).strip()

def load(path:str):
    with urlopen(BASE+path, timeout=30) as r: return json.loads(r.read().decode('utf-8'))

def fmt_price(v):
    return f"{int(v):,}".replace(',', '\u202f')+' €/mois' if v else 'Prix n.c.'

def fmt_surface(v):
    try:
        f=float(v); return (str(int(f)) if f.is_integer() else str(f).rstrip('0').rstrip('.'))+' m²'
    except Exception: return str(v)+' m²'

def expect(label, needle, hay, errors):
    if needle and norm(needle) not in norm(hay): errors.append(f'{label}: {needle!r} absent de {hay[:180]!r}')

listings=load('/listings.json')['listings']; by_id={str(x['id']):x for x in listings}; errors=[]; evidence=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':390,'height':844})
    page.goto(BASE+'/?rev=data-truth', wait_until='networkidle', timeout=45000)
    page.evaluate('localStorage.clear(); sessionStorage.clear();')
    page.reload(wait_until='networkidle')
    cards=page.locator('.card').evaluate_all("""els=>els.slice(0,30).map(c=>({id:c.dataset.id,text:c.innerText,href:c.querySelector('a[data-stop-card]')?.href||''}))""")
    for c in cards:
        x=by_id.get(str(c['id']))
        if not x: errors.append(f"carte inconnue {c['id']}"); continue
        expect(c['id']+' prix', fmt_price(x.get('price')), c['text'], errors)
        expect(c['id']+' lieu', x.get('location') or x.get('city') or '', c['text'], errors)
        expect(c['id']+' titre', (x.get('title') or '')[:55], c['text'], errors)
        expect(c['id']+' type', x.get('type') or '', c['text'], errors)
        if x.get('surface'): expect(c['id']+' surface', fmt_surface(x.get('surface')), c['text'], errors)
        if x.get('rooms'): expect(c['id']+' pieces', f"{x.get('rooms')} p.", c['text'], errors)
        if x.get('bedrooms'): expect(c['id']+' chambres', f"{x.get('bedrooms')} ch.", c['text'], errors)
        if c['href'] and x.get('url') and c['href'].split('#')[0] != x['url'].split('#')[0]: errors.append(f"{c['id']} href mismatch")
    ids=[c['id'] for c in cards[:12]]
    for cid in ids:
        page.click(f'.card[data-id="{cid}"] [data-open]')
        page.wait_for_timeout(120)
        text=page.locator('#modal').inner_text()
        x=by_id[str(cid)]
        expect('modal '+cid+' prix', fmt_price(x.get('price')), text, errors)
        expect('modal '+cid+' lieu', x.get('location') or x.get('city') or '', text, errors)
        expect('modal '+cid+' titre', (x.get('title') or '')[:55], text, errors)
        if x.get('surface'): expect('modal '+cid+' surface', fmt_surface(x.get('surface')), text, errors)
        if x.get('rooms'): expect('modal '+cid+' pieces', f"{x.get('rooms')} pièces", text, errors)
        page.click('#closeModal')
    evidence={'cards_checked':len(cards),'modals_checked':len(ids),'sample_ids':ids[:5]}
    browser.close()
print(json.dumps({'ok':not errors,'base':BASE,'errors':errors[:120],'error_count':len(errors),'evidence':evidence}, ensure_ascii=False, indent=2))
if errors: raise SystemExit(1)
print('DATA_TRUTH_PUBLIC_AUDIT PASS')
