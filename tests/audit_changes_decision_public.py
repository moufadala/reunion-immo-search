#!/usr/bin/env python3
"""Public audit for decision-oriented changes page.

Checks that advertised price movements are actually visible near the top of the
public page and not hidden behind disappeared cards.
"""
from __future__ import annotations
import json, os
from urllib.request import urlopen
from playwright.sync_api import sync_playwright
BASE = os.environ.get('IMMO_PUBLIC_BASE', 'https://immo.148.230.103.174.sslip.io').rstrip('/')


def expected() -> dict[str, int]:
    with urlopen(BASE + '/changes.json', timeout=25) as r:
        data=json.loads(r.read())
    changes=data.get('changes') or []
    return {
        'all': len(changes),
        'price_down': sum(1 for x in changes if x.get('event_type')=='price_changed' and (x.get('direction')=='down' or (x.get('new_value') is not None and x.get('old_value') is not None and float(x.get('new_value')) < float(x.get('old_value'))))),
        'reappeared': sum(1 for x in changes if x.get('event_type')=='reappeared'),
        'disappeared': sum(1 for x in changes if x.get('event_type')=='disappeared'),
    }


def main() -> int:
    errors=[]
    exp=expected()
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True)
        page=b.new_page(viewport={'width':390,'height':844})
        page.goto(BASE + '/changes.html?rev=decision-audit', wait_until='networkidle')
        st=page.evaluate("""() => ({
          hasBrief: !!document.querySelector('#decisionBrief'),
          h2: document.querySelector('#decisionBrief h2')?.innerText || '',
          priceCards: document.querySelectorAll('#decisionBrief .decision-price_down').length,
          reappearedCards: document.querySelectorAll('#decisionBrief .decision-reappeared').length,
          cards: document.querySelectorAll('#decisionBrief .decision-card').length,
          firstCard: document.querySelector('#decisionBrief .decision-card')?.innerText || '',
          topText: document.body.innerText.slice(0,1600),
          rawFirstTypes: [...document.querySelectorAll('.change-card')].slice(0,12).map(c => ({type:c.dataset.type, direction:c.dataset.direction, text:c.innerText.slice(0,120)})),
          scrollWidth: document.documentElement.scrollWidth,
          width: window.innerWidth,
          buttons: [...document.querySelectorAll('[data-filter]')].map(x=>x.innerText).join(' '),
        })""")
        if not st['hasBrief']:
            errors.append('decision brief missing')
        if 'priorité' not in st['topText'].lower() and 'regarder' not in st['topText'].lower():
            errors.append('decision language missing')
        if exp['price_down'] and st['priceCards'] != min(8, exp['price_down']):
            errors.append(f"decision brief price_down count mismatch expected {min(8, exp['price_down'])}, got {st['priceCards']}")
        if exp['reappeared'] and st['reappearedCards'] < min(1, exp['reappeared']):
            errors.append('decision brief has no reappeared card despite data')
        for token in [str(exp['price_down']), 'baisses', str(exp['reappeared']), 'réapparitions', str(exp['disappeared']), 'disparues']:
            if token not in st['topText']:
                errors.append(f'decision summary missing token {token!r}')
        if exp['price_down'] and not any(x['type']=='price_changed' and x['direction']=='down' for x in st['rawFirstTypes'][:min(8, exp['price_down'])]):
            errors.append('raw page top does not expose price drops before disappeared list')
        if st['scrollWidth'] > st['width'] + 2:
            errors.append(f"mobile overflow {st['scrollWidth']} > {st['width']}")
        b.close()
    out={'ok': not errors, 'base': BASE, 'expected': exp, 'errors': errors, 'state': st}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)
    print('CHANGES_DECISION_PUBLIC_AUDIT PASS')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
