#!/usr/bin/env python3
"""Deep public QA for the clean immo portal.

Acts like a strict user/QA engineer:
- runs many natural-search queries in the public mobile UI;
- verifies visible card data against public listings.json (source of truth for the static app);
- checks modal/detail data against the same listing object;
- checks forbidden technical/ugly tokens do not leak;
- checks changes.html filters against changes.json.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

BASE = os.environ.get('IMMO_PUBLIC_BASE', 'https://immo.148.230.103.174.sslip.io').rstrip('/')
HOME = BASE + '/?rev=deep-qa'
CHANGES = BASE + '/changes.html?rev=deep-qa'
FORBIDDEN = [
    'serp_view', 'distributionTypes', 'CDP', 'sdb/eau haut', 'sdb/eau bas',
    '3 ch. bas', 'No\n', 'undefined', 'null', '[object Object]',
]
QUERIES = [
    '', 'rivière des pluies', 'rivieres des pluies', 'beausejour', 'beauséjour',
    'grande montée', 'grande montee', 'duparc', 'bretagne', 'la bretagne',
    'T4', 'F4', 'T2', 'F2', 'studio', 'non meublé', 'pas meublé', 'sans meublé',
    'meublé', '900', 'moins 900', 'plus 900', '= 900', 'autour de 900',
    'entre 800 et 1000', 'F4 non meublé Beauséjour 900',
    'F4 non meublé Beauséjour moins 900', 'T4 non meublé Grande Montée moins 1300',
    'rivière des pluies T5', 'duparc appartement', 'bretagne T3',
    'parking saint denis moins 900', 'maison grande montée', 'appartement beausejour',
]


def norm(s: Any) -> str:
    return re.sub(r'\s+', ' ', ''.join(c for c in unicodedata.normalize('NFD', str(s or '').lower()) if unicodedata.category(c) != 'Mn')).strip()


def fmt_price(v: Any) -> str:
    if not v:
        return 'Prix n.c.'
    return f"{int(v):,}".replace(',', '\u202f') + ' €/mois'


def clean_text(s: str) -> str:
    return re.sub(r'\s+', ' ', s or '').strip()


def fmt_surface(v: Any) -> str:
    try:
        f = float(v)
        return (str(int(f)) if f.is_integer() else str(f).rstrip('0').rstrip('.')) + ' m²'
    except Exception:
        return f"{v} m²"


def load_json(url: str) -> Any:
    with urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def expect_subset(label: str, needle: str, hay: str, errors: list[str]) -> None:
    if needle and norm(needle) not in norm(hay):
        errors.append(f"{label}: attendu {needle!r} absent de {hay[:220]!r}")


def source_hay(x: dict[str, Any]) -> str:
    li = x.get('location_intelligence') or {}
    return ' '.join(str(v or '') for v in [
        x.get('id'), x.get('source_id'), x.get('title'), x.get('description'),
        x.get('location'), x.get('city'), x.get('district'), li.get('district_best'),
        li.get('precise_location_label'), li.get('commune_inferred'), ' '.join(li.get('district_hints') or []),
    ])


def card_expectations(card: dict[str, Any], x: dict[str, Any], errors: list[str]) -> None:
    txt = card['text']
    if card['id'] != str(x.get('id')):
        errors.append(f"card id mismatch DOM={card['id']} JSON={x.get('id')}")
    expect_subset(f"{x.get('id')} prix", fmt_price(x.get('price')), txt, errors)
    expect_subset(f"{x.get('id')} lieu", x.get('location') or x.get('city') or '', txt, errors)
    title = clean_text(x.get('title') or 'Annonce immobilière')[:60]
    expect_subset(f"{x.get('id')} titre", title, txt, errors)
    expect_subset(f"{x.get('id')} type", x.get('type') or 'Bien', txt, errors)
    if x.get('surface'):
        expect_subset(f"{x.get('id')} surface", fmt_surface(x.get('surface')), txt, errors)
    if x.get('rooms'):
        expect_subset(f"{x.get('id')} pièces", f"{x.get('rooms')} p.", txt, errors)
    if x.get('bedrooms'):
        expect_subset(f"{x.get('id')} chambres", f"{x.get('bedrooms')} ch.", txt, errors)
    if card.get('sourceHref') and x.get('url') and card['sourceHref'].split('#')[0] != x['url'].split('#')[0]:
        errors.append(f"{x.get('id')} lien source mismatch DOM={card['sourceHref']} JSON={x.get('url')}")
    for bad in FORBIDDEN:
        if bad.lower() in txt.lower():
            errors.append(f"{x.get('id')} token interdit visible carte: {bad}")


def modal_expectations(modal: dict[str, Any], x: dict[str, Any], errors: list[str]) -> None:
    text = modal['text']
    expect_subset(f"modal {x.get('id')} prix", fmt_price(x.get('price')), text, errors)
    expect_subset(f"modal {x.get('id')} lieu", x.get('location') or x.get('city') or '', text, errors)
    expect_subset(f"modal {x.get('id')} titre", clean_text(x.get('title') or '')[:60], text, errors)
    # The modal intentionally strips source/debug boilerplate from descriptions.
    # Verify core facts instead of requiring the raw source description verbatim.
    if x.get('surface'):
        expect_subset(f"modal {x.get('id')} surface", fmt_surface(x.get('surface')), text, errors)
    if x.get('rooms'):
        expect_subset(f"modal {x.get('id')} pièces", f"{x.get('rooms')} pièces", text, errors)
    for bad in FORBIDDEN:
        if bad.lower() in text.lower():
            errors.append(f"{x.get('id')} token interdit visible modal: {bad}")


def main() -> int:
    listings_payload = load_json(BASE + '/listings.json')
    listings = listings_payload['listings']
    by_id = {str(x['id']): x for x in listings}
    changes_payload = load_json(BASE + '/changes.json')
    change_items = changes_payload.get('changes') or changes_payload.get('items') or []
    errors: list[str] = []
    evidence: dict[str, Any] = {'queries': [], 'cards_checked': 0, 'modals_checked': 0, 'changes': {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        console: list[str] = []
        page.on('console', lambda m: console.append(f'{m.type}: {m.text}'))
        page.goto(HOME, wait_until='networkidle', timeout=45000)
        page.evaluate("localStorage.clear(); sessionStorage.clear();")
        page.reload(wait_until='networkidle')

        for q in QUERIES:
            page.fill('#q', q)
            page.wait_for_timeout(450)
            data = page.evaluate("""() => ({
                q: document.querySelector('#q').value,
                summary: document.querySelector('#summary')?.textContent || '',
                understood: document.querySelector('#understood')?.textContent || '',
                chips: [...document.querySelectorAll('#nlChips .nlchip')].map(x=>x.innerText.trim().split('\\n').join(' ')),
                ids: [...document.querySelectorAll('.card')].slice(0,8).map(c=>c.dataset.id),
                cards: [...document.querySelectorAll('.card')].slice(0,5).map(c=>({
                    id:c.dataset.id,
                    text:c.innerText,
                    sourceHref:c.querySelector('a[data-stop-card]')?.href || '',
                    img:c.querySelector('img')?.getAttribute('src') || ''
                })),
                horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth
            })""")
            if data['horizontalOverflow']:
                errors.append(f"overflow mobile sur {q!r}: {data['scrollWidth']} > {data['innerWidth']}")
            if q in ('T4', 'F4') and 'T/F4' not in ' '.join(data['chips']):
                errors.append(f"{q}: chip T/F4 absent {data['chips']}")
            if q == '900' and not any('900 € ?' in c and '≤' in c and '≥' in c and '=' in c for c in data['chips']):
                errors.append("900: choix budget explicite absent")
            if q in ('duparc', 'bretagne', 'la bretagne') and '581 correspondances' in data['summary']:
                errors.append(f"{q}: élargissement silencieux à toute la base")
            if q.startswith('non meubl') or q.startswith('pas meubl') or q.startswith('sans meubl'):
                if 'Non meublé' not in ' '.join(data['chips']):
                    errors.append(f"{q}: chip Non meublé absent")
            for c in data['cards']:
                x = by_id.get(str(c['id']))
                if not x:
                    errors.append(f"{q}: carte DOM id inconnue {c['id']}")
                    continue
                card_expectations(c, x, errors)
                evidence['cards_checked'] += 1
            # Exact quartier sanity: if results exist, each first ids must be backed by the queried word in JSON haystack.
            exact_alias = {
                'beausejour':['beausejour'], 'beauséjour':['beausejour'], 'grande montée':['grande montee'], 'grande montee':['grande montee'],
                'rivière des pluies':['riviere des pluies'], 'rivieres des pluies':['riviere des pluies','rivieres des pluies'],
                'duparc':['duparc'], 'bretagne':['bretagne'], 'la bretagne':['bretagne'],
            }.get(q)
            if exact_alias:
                for cid in data['ids']:
                    x = by_id.get(str(cid)); h = norm(source_hay(x or {}))
                    if x and not any(a in h for a in exact_alias):
                        errors.append(f"{q}: résultat {cid} non justifié par JSON/source haystack")
            evidence['queries'].append({k: data[k] for k in ('q','summary','understood','chips','ids')})

        # Test budget operator click with data truth kept.
        page.fill('#q', 'F4 non meublé Beauséjour 900')
        page.wait_for_timeout(400)
        page.click('[data-price-op="max"]')
        page.wait_for_timeout(400)
        op = page.evaluate("() => ({q:document.querySelector('#q').value, chips:[...document.querySelectorAll('#nlChips .nlchip')].map(x=>x.innerText.trim().split('\\\\n').join(' ')), understood:document.querySelector('#understood').textContent, summary:document.querySelector('#summary').textContent})")
        if op['q'] != 'F4 non meublé Beauséjour moins 900' or not any('≤ 900 €' in c for c in op['chips']):
            errors.append(f"clic ≤ budget ne met pas à jour correctement: {op}")
        evidence['operator'] = op

        # Modal checks on first 12 default cards.
        page.fill('#q', '')
        page.wait_for_timeout(500)
        ids = page.evaluate("() => [...document.querySelectorAll('.card')].slice(0,12).map(c=>c.dataset.id)")
        for cid in ids:
            page.click(f'.card[data-id="{cid}"] [data-open]')
            page.wait_for_timeout(180)
            modal = page.evaluate("""() => ({
                open: document.querySelector('#modal')?.classList.contains('open'),
                text: document.querySelector('#modal')?.innerText || '',
                sourceHref: document.querySelector('#mSource')?.href || ''
            })""")
            if not modal['open']:
                errors.append(f"modal {cid}: ne s'ouvre pas")
            else:
                modal_expectations(modal, by_id[str(cid)], errors)
                evidence['modals_checked'] += 1
            page.click('#closeModal')
            page.wait_for_timeout(80)

        # Changes filters + JSON comparison.
        cp = browser.new_page(viewport={'width':390,'height':844})
        cp.goto(CHANGES, wait_until='networkidle', timeout=45000)
        counts_json = {
            'all': len(change_items),
            'price_down': sum(1 for x in change_items if x.get('event_type') == 'price_changed' and x.get('direction') == 'down'),
            'price_up': sum(1 for x in change_items if x.get('event_type') == 'price_changed' and x.get('direction') == 'up'),
            'disappeared': sum(1 for x in change_items if x.get('event_type') == 'disappeared'),
            'reappeared': sum(1 for x in change_items if x.get('event_type') == 'reappeared'),
        }
        for filt, expected in counts_json.items():
            cp.click(f'[data-filter="{filt}"]')
            cp.wait_for_timeout(150)
            visible = cp.locator('.change-card:not([hidden])').count()
            first = cp.locator('.change-card:not([hidden])').evaluate_all("els => els.slice(0,5).map(e => ({type:e.dataset.type, direction:e.dataset.direction, text:e.innerText}))")
            evidence['changes'][filt] = {'expected_json': expected, 'visible_dom': visible, 'first': first}
            if visible != expected:
                errors.append(f"changes {filt}: DOM {visible} != JSON {expected}")
            for item in first:
                for bad in FORBIDDEN:
                    if bad.lower() in item['text'].lower():
                        errors.append(f"changes {filt}: token interdit {bad}")
        browser.close()

    real_console_errors = [c for c in console if c.startswith('error') and 'Failed to load resource' not in c]
    if real_console_errors:
        errors.extend([f"console: {c}" for c in real_console_errors[-10:]])
    out = {'ok': not errors, 'base': BASE, 'errors': errors[:200], 'error_count': len(errors), 'evidence': evidence}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not errors else 1

if __name__ == '__main__':
    raise SystemExit(main())
