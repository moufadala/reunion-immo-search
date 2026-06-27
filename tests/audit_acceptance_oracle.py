#!/usr/bin/env python3
"""Executable product oracle for Immo RUN natural search.

This is intentionally business-facing: Moufadal gives examples, the oracle expands
families and verifies public UX + JSON-backed truth.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

BASE = os.environ.get('IMMO_PUBLIC_BASE', 'https://immo.148.230.103.174.sslip.io').rstrip('/')
ORACLE = Path(os.environ.get('IMMO_ACCEPTANCE_ORACLE', 'tests/acceptance_user_search_cases.json'))


def norm(s: Any) -> str:
    return re.sub(r'\s+', ' ', ''.join(
        c for c in unicodedata.normalize('NFD', str(s or '').lower())
        if unicodedata.category(c) != 'Mn'
    )).strip()


def count_from_summary(summary: str) -> int | None:
    m = re.search(r'(\d+)\s+affich[ée]s?\s+sur\s+(\d+)\s+correspondances', summary or '', re.I)
    if m:
        return int(m.group(2))
    m = re.search(r'^(\d+)\s+r[ée]sultat', summary or '', re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s+correspondances', summary or '', re.I)
    if m:
        return int(m.group(1))
    return None


def load_url_json(path: str) -> Any:
    with urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def source_hay(x: dict[str, Any]) -> str:
    li = x.get('location_intelligence') or {}
    return norm(' '.join(str(v or '') for v in [
        x.get('id'), x.get('source_id'), x.get('title'), x.get('description'),
        x.get('location'), x.get('city'), x.get('district'), x.get('region'),
        li.get('district_best'), li.get('precise_location_label'),
        li.get('commune_inferred'), ' '.join(li.get('district_hints') or []),
        ' '.join(x.get('feature_tags') or []), x.get('furnished'), x.get('type'),
    ]))


def card_data(page) -> dict[str, Any]:
    return page.evaluate("""() => ({
      summary: document.querySelector('#summary')?.textContent || '',
      understood: document.querySelector('#understood')?.textContent || '',
      q: document.querySelector('#q')?.value || '',
      chips: [...document.querySelectorAll('#nlChips .nlchip')].map(x => x.innerText.trim().split('\\n').join(' ')),
      priceOps: [...document.querySelectorAll('[data-price-op]')].map(x => x.dataset.priceOp),
      overflow: document.documentElement.scrollWidth > window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      cards: [...document.querySelectorAll('.card')].slice(0, 12).map(c => ({
        id: c.dataset.id,
        text: c.innerText
      }))
    })""")


def chips_contain(chips: list[str], needles: list[str]) -> list[str]:
    joined = norm(' | '.join(chips))
    missing = []
    for n in needles:
        if norm(n) not in joined:
            missing.append(n)
    return missing


def check_case(case: dict[str, Any], data: dict[str, Any], by_id: dict[str, dict[str, Any]], total: int) -> list[str]:
    errors: list[str] = []
    cid = case['id']
    count = count_from_summary(data['summary'])

    for bad in case.get('global_must_not_contain', []):
        pass
    if data['overflow']:
        errors.append(f"{cid}: overflow mobile {data['scrollWidth']} > {data['innerWidth']}")
    if case.get('must_not_return_all') and count == total:
        errors.append(f"{cid}: renvoie toute la base silencieusement")
    if 'expect_min_count' in case and (count is None or count < int(case['expect_min_count'])):
        errors.append(f"{cid}: count {count} < {case['expect_min_count']} summary={data['summary']!r}")
    if 'expect_max_count' in case and (count is None or count > int(case['expect_max_count'])):
        errors.append(f"{cid}: count {count} > {case['expect_max_count']} summary={data['summary']!r}")
    if case.get('expect_summary_contains') and norm(case['expect_summary_contains']) not in norm(data['summary']):
        errors.append(f"{cid}: summary ne contient pas {case['expect_summary_contains']!r}: {data['summary']!r}")
    if case.get('expect_understood_contains') and norm(case['expect_understood_contains']) not in norm(data['understood']):
        errors.append(f"{cid}: understood ne contient pas {case['expect_understood_contains']!r}: {data['understood']!r}")
    missing = chips_contain(data['chips'], case.get('expect_chips_any') or [])
    if missing:
        errors.append(f"{cid}: chips manquants {missing}, chips={data['chips']}")
    expected_ops = set(case.get('expect_price_operator_buttons') or [])
    if expected_ops and not expected_ops.issubset(set(data['priceOps'])):
        errors.append(f"{cid}: boutons budget manquants {sorted(expected_ops - set(data['priceOps']))}, ops={data['priceOps']}")

    cards = data['cards']
    if case.get('must_not_filter_by_budget'):
        # A bare number should not reduce the result set to <= or >=; current product keeps all until user chooses.
        if count is not None and count < total:
            errors.append(f"{cid}: nombre nu filtre déjà {count}/{total}")
    if case.get('expect_no_card_terms'):
        for term in case['expect_no_card_terms']:
            for c in cards[:5]:
                # Avoid false-positive on text generated by buttons/actions; this is still intentionally user-visible.
                if norm(term) in norm(c['text']):
                    errors.append(f"{cid}: terme interdit visible dans carte {c['id']}: {term}")
                    break
    if case.get('expect_source_terms_any'):
        terms = [norm(t) for t in case['expect_source_terms_any']]
        for c in cards:
            x = by_id.get(str(c['id']))
            if x and not any(t in source_hay(x) for t in terms):
                errors.append(f"{cid}: carte {c['id']} non justifiée par source terms {terms}")
    if case.get('expect_rooms') and cards:
        expected_rooms = int(case['expect_rooms'])
        for c in cards:
            x = by_id.get(str(c['id']))
            if x and int(x.get('rooms') or 0) != expected_rooms:
                errors.append(f"{cid}: carte {c['id']} rooms JSON={x.get('rooms')} attendu={expected_rooms}")
                break
    for bound_key, op in [('expect_price_max', '<='), ('expect_price_min', '>=')]:
        if bound_key in case:
            bound = int(case[bound_key])
            for c in cards:
                x = by_id.get(str(c['id']))
                price = x.get('price') if x else None
                if price is None:
                    continue
                if op == '<=' and int(price) > bound:
                    errors.append(f"{cid}: prix {price} > {bound} sur {c['id']}")
                    break
                if op == '>=' and int(price) < bound:
                    errors.append(f"{cid}: prix {price} < {bound} sur {c['id']}")
                    break
    if 'expect_price_between' in case:
        lo, hi = case['expect_price_between']
        for c in cards:
            x = by_id.get(str(c['id']))
            price = x.get('price') if x else None
            if price is None:
                continue
            if not (int(lo) <= int(price) <= int(hi)):
                errors.append(f"{cid}: prix {price} hors [{lo},{hi}] sur {c['id']}")
                break
    return errors


def main() -> int:
    oracle = json.loads(ORACLE.read_text(encoding='utf-8'))
    listings = load_url_json('/listings.json')['listings']
    by_id = {str(x['id']): x for x in listings}
    total = len(listings)
    errors: list[str] = []
    evidence: list[dict[str, Any]] = []
    group_counts: dict[str, int] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        console: list[str] = []
        page.on('console', lambda m: console.append(f'{m.type}: {m.text}'))
        page.goto(BASE + '/?rev=acceptance-oracle', wait_until='networkidle', timeout=45000)
        page.evaluate('localStorage.clear(); sessionStorage.clear();')
        page.reload(wait_until='networkidle')

        for case in oracle['cases']:
            page.fill('#q', case['query'])
            page.dispatch_event('#q', 'input')
            page.wait_for_timeout(450)
            data = card_data(page)
            data_errors = check_case(case, data, by_id, total)
            errors.extend(data_errors)
            count = count_from_summary(data['summary'])
            if case.get('group_equal') and count is not None:
                prev = group_counts.setdefault(case['group_equal'], count)
                if prev != count:
                    errors.append(f"{case['id']}: groupe {case['group_equal']} count {count} != {prev}")
            evidence.append({
                'id': case['id'], 'query': case['query'], 'summary': data['summary'],
                'understood': data['understood'], 'chips': data['chips'],
                'first_ids': [c['id'] for c in data['cards'][:5]], 'errors': data_errors[:5]
            })

        for inter in oracle.get('interactions', []):
            page.fill('#q', inter['start_query'])
            page.dispatch_event('#q', 'input')
            page.wait_for_timeout(300)
            selector = f"[data-price-op=\"{inter['click_price_op']}\"]"
            if page.locator(selector).count() == 0:
                errors.append(f"{inter['id']}: bouton absent {selector}")
                continue
            page.click(selector)
            page.wait_for_timeout(350)
            data = card_data(page)
            if norm(inter['expect_query']) not in norm(data['q']):
                errors.append(f"{inter['id']}: query {data['q']!r} ne contient pas {inter['expect_query']!r}")
            missing = chips_contain(data['chips'], inter.get('expect_chips_any') or [])
            if missing:
                errors.append(f"{inter['id']}: chips manquants après clic {missing}, chips={data['chips']}")
            evidence.append({'id': inter['id'], 'query': data['q'], 'chips': data['chips'], 'summary': data['summary']})

        real_console_errors = [c for c in console if c.startswith('error') and 'Failed to load resource' not in c]
        errors.extend([f'console: {c}' for c in real_console_errors[-10:]])
        browser.close()

    out = {
        'ok': not errors,
        'base': BASE,
        'oracle': str(ORACLE),
        'cases': len(oracle['cases']),
        'interactions': len(oracle.get('interactions', [])),
        'errors': errors[:200],
        'error_count': len(errors),
        'evidence_sample': evidence[:20],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if errors:
        return 1
    print('ACCEPTANCE_ORACLE_AUDIT PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
