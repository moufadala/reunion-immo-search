#!/usr/bin/env python3
"""Browser-level audit for public changes.html filters.

The expected counts are derived from the delivered changes.json, not hardcoded,
so the gate keeps validating truth when the watch data changes.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.browser_qa_runtime import launch_chromium

URL=os.environ.get('IMMO_CHANGES_URL','https://immo.148.230.103.174.sslip.io/changes.html?rev=changes-audit')
BASE=URL.split('/changes.html',1)[0]


def expected_counts() -> dict[str, int]:
    with urlopen(BASE + '/changes.json', timeout=25) as r:
        data=json.loads(r.read())
    changes=data.get('changes') or []
    return {
        'all': len(changes),
        'price_down': sum(1 for x in changes if x.get('event_type')=='price_changed' and (x.get('direction')=='down' or (x.get('new_value') is not None and x.get('old_value') is not None and float(x.get('new_value')) < float(x.get('old_value'))))),
        'price_up': sum(1 for x in changes if x.get('event_type')=='price_changed' and (x.get('direction')=='up' or (x.get('new_value') is not None and x.get('old_value') is not None and float(x.get('new_value')) > float(x.get('old_value'))))),
        'disappeared': sum(1 for x in changes if x.get('event_type')=='disappeared'),
        'reappeared': sum(1 for x in changes if x.get('event_type')=='reappeared'),
    }

EXPECT=expected_counts()
errors=[]
with sync_playwright() as p:
    browser=launch_chromium(p)
    page=browser.new_page(viewport={'width':390,'height':844})
    console=[]
    page.on('console', lambda m: console.append(f'{m.type}: {m.text}'))
    page.goto(URL, wait_until='networkidle', timeout=45000)
    data={}
    initial_first=page.locator('.change-card:not([hidden])').evaluate_all("els => els.slice(0,12).map(e => ({type:e.dataset.type, direction:e.dataset.direction, text:e.innerText.slice(0,120)}))")
    for filt, expected in EXPECT.items():
        page.click(f'[data-filter="{filt}"]')
        page.wait_for_timeout(250)
        visible=page.locator('.change-card:not([hidden])').count()
        display_visible=page.locator('.change-card').evaluate_all("els => els.filter(e => getComputedStyle(e).display !== 'none' && !e.hidden).length")
        label=page.locator('#countLabel').inner_text()
        first_types=page.locator('.change-card:not([hidden])').evaluate_all("els => els.slice(0,8).map(e => ({type:e.dataset.type, direction:e.dataset.direction, text:e.innerText.slice(0,120)}))")
        data[filt]={'expected':expected,'visible':visible,'display_visible':display_visible,'label':label,'first':first_types}
        if visible != expected or display_visible != expected:
            errors.append(f'{filt}: attendu {expected}, visible={visible}, display_visible={display_visible}, label={label!r}')
        if expected and str(expected) not in label:
            errors.append(f'{filt}: compteur utilisateur ne contient pas {expected}: {label!r}')
        if filt=='price_down' and any(x['type']!='price_changed' or x['direction']!='down' for x in first_types):
            errors.append('price_down affiche autre chose que des baisses dans les premières cartes')
        if filt=='price_up' and any(x['type']!='price_changed' or x['direction']!='up' for x in first_types):
            errors.append('price_up affiche autre chose que des hausses dans les premières cartes')
        if filt=='disappeared' and any(x['type']!='disappeared' for x in first_types):
            errors.append('disappeared affiche autre chose que des disparues dans les premières cartes')
        if filt=='reappeared' and any(x['type']!='reappeared' for x in first_types):
            errors.append('reappeared affiche autre chose que des réapparues dans les premières cartes')
    if EXPECT['price_down']:
        non_disappeared_first=sum(1 for x in initial_first[:min(8, EXPECT['price_down'])] if x['type']!='disappeared')
        if non_disappeared_first < min(3, EXPECT['price_down']):
            errors.append('Au chargement, les signaux utiles sont noyés par les disparues')
    browser.close()
print(json.dumps({'ok':not errors,'url':URL,'expected':EXPECT,'errors':errors,'data':data,'initial_first':initial_first,'console':console[-20:]}, ensure_ascii=False, indent=2))
sys.exit(0 if not errors else 1)
