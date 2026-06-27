#!/usr/bin/env python3
"""Broad public search oracle V2 for Immo RUN.

Generated cases are intentionally tolerant on exact ranking but strict on UX invariants:
- no JS crash;
- parsed intent appears in chips/criteria;
- known structured queries do not silently return the whole base;
- ambiguous bare budgets offer operator buttons;
- budget buttons synchronize query and criteria.
"""
from __future__ import annotations
import json, os, re, unicodedata
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get('IMMO_PUBLIC_BASE', 'https://immo.148.230.103.174.sslip.io').rstrip('/')
ORACLE = Path(os.environ.get('IMMO_ORACLE_V2', ROOT / 'tests' / 'acceptance_search_oracle_v2.json'))
MAX_CASES = int(os.environ.get('IMMO_ORACLE_V2_MAX_CASES', '260'))


def norm(s: Any) -> str:
    s = str(s or '').lower()
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def count_from_summary(summary: str) -> int | None:
    m = re.search(r'(\d+)\s+(?:résultat|affiché)', summary.replace('\u202f', ' '))
    return int(m.group(1)) if m else None


def page_state(page) -> dict[str, Any]:
    return page.evaluate("""() => ({
      summary: document.querySelector('#summary')?.innerText || '',
      understood: document.querySelector('#understood')?.innerText || '',
      chips: [...document.querySelectorAll('#nlChips .nlchip')].map(x => x.innerText.trim().split('\\n').join(' ')),
      cardCount: document.querySelectorAll('.card').length,
      ids: [...document.querySelectorAll('.card')].slice(0,20).map(x => x.dataset.id || ''),
      priceButtons: [...document.querySelectorAll('.priceChoice button')].map(b => ({op:b.dataset.op, text:b.innerText, rect:b.getBoundingClientRect().toJSON()})),
      bodyText: document.body.innerText.slice(0,800),
      q: document.querySelector('#q')?.value || '',
    })""")


def has_any(hay: str, needles: list[str]) -> bool:
    hn = norm(hay)
    return any(norm(n) in hn for n in needles if n)


def item_hay(x: dict[str, Any]) -> str:
    li = x.get('location_intelligence') or {}
    return ' '.join(str(v or '') for v in [
        x.get('id'), x.get('source_id'), x.get('title'), x.get('description'),
        x.get('city'), x.get('district'), x.get('location'), x.get('region'),
        li.get('district_best'), li.get('precise_location_label'), li.get('commune_inferred'),
        ' '.join(li.get('district_hints') or []),
    ])


def main() -> int:
    oracle = json.loads(ORACLE.read_text())
    with urlopen(BASE + '/listings.json', timeout=25) as r:
        payload = json.loads(r.read()) or {}
        items = payload.get('listings') or []
        total = len(items)
        by_id = {str(x.get('id')): x for x in items}
    cases = oracle.get('cases', [])[:MAX_CASES]
    errors: list[str] = []
    evidence: list[dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.goto(BASE + '/?rev=oracle-v2', wait_until='networkidle')
        page.wait_for_selector('#q')
        page.wait_for_selector('.card')
        page.wait_for_timeout(500)
        for case in cases:
            q = case['query']
            exp = case.get('expect') or {}
            page.click('#q')
            page.keyboard.press('Control+A')
            page.keyboard.press('Backspace')
            if q:
                page.keyboard.type(q, delay=1)
            page.wait_for_timeout(250)
            page.wait_for_function("q => document.querySelector('#q') && document.querySelector('#q').value === q", arg=q, timeout=3000)
            st = page_state(page)
            summary = st['summary']
            understood = st['understood']
            merged = ' '.join([understood, ' '.join(st['chips'])])
            cnt = count_from_summary(summary)
            ce: list[str] = []
            if 'Erreur' in st['bodyText'] or 'undefined' in merged.lower():
                ce.append('ui_crash_or_undefined')
            if exp.get('understood_any') and not has_any(merged, exp['understood_any']):
                ce.append(f"missing_understood_any={exp['understood_any']} got={merged[:160]}")
            for bad in exp.get('not_understood_any') or []:
                # Check forbidden tokens on chips only, not substring inside "Non meublé".
                badn = norm(bad)
                chip_norms = [norm(c.replace('×','').strip()) for c in st['chips']]
                if badn in chip_norms:
                    ce.append(f"forbidden_understood={bad} got={merged[:160]}")
            if exp.get('has_price_buttons'):
                ops = {b.get('op') for b in st['priceButtons']}
                if not {'max','min','equal','around'} <= ops:
                    ce.append(f'missing_price_buttons={ops}')
            if exp.get('no_zero') and cnt == 0:
                ce.append('unexpected_zero_result')
            if exp.get('no_all_base') and cnt == total:
                ce.append('unexpected_all_base_result')
            if exp.get('result_hay_any') and st.get('ids'):
                for cid in st['ids'][:10]:
                    x = by_id.get(str(cid))
                    if x and not has_any(item_hay(x), exp['result_hay_any']):
                        ce.append(f"result_not_justified id={cid} expected_any={exp['result_hay_any'][:5]}")
                        break
            if ce:
                errors.append(f"{case['id']} query={q!r}: " + '; '.join(ce))
            if len(evidence) < 30 or ce:
                evidence.append({'id': case['id'], 'query': q, 'family': case.get('family'), 'summary': summary, 'understood': understood, 'chips': st['chips'], 'count': cnt, 'errors': ce})

        for it in oracle.get('interaction_cases', []):
            page.click('#q')
            page.keyboard.press('Control+A')
            page.keyboard.press('Backspace')
            page.keyboard.type(it['start'], delay=1)
            page.wait_for_timeout(250)
            btn = page.locator(f".priceChoice button[data-op='{it['click']}']")
            if btn.count() == 0:
                errors.append(f"interaction {it['id']}: button missing")
                continue
            btn.first.click()
            page.wait_for_timeout(180)
            st = page_state(page)
            ce = []
            if it.get('expect_query') and norm(st['q']) != norm(it['expect_query']):
                ce.append(f"query_sync expected {it['expect_query']} got {st['q']}")
            if it.get('expect_understood_any') and not has_any(st['understood'] + ' ' + ' '.join(st['chips']), it['expect_understood_any']):
                ce.append(f"criteria_sync expected {it['expect_understood_any']} got {st['understood']}")
            if ce:
                errors.append(f"interaction {it['id']}: " + '; '.join(ce))
            evidence.append({'id': it['id'], 'query': st['q'], 'summary': st['summary'], 'understood': st['understood'], 'errors': ce})
        browser.close()

    out = {'ok': not errors, 'base': BASE, 'oracle': str(ORACLE), 'total_listings': total, 'cases_run': len(cases), 'interactions_run': len(oracle.get('interaction_cases', [])), 'error_count': len(errors), 'errors': errors[:80], 'evidence_sample': evidence[:40]}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)
    print('SEARCH_ORACLE_V2_AUDIT PASS')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
