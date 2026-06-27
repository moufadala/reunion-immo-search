#!/usr/bin/env python3
import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright

URL = 'https://immo.148.230.103.174.sslip.io/?rev=audit-searchv3'
CASES = [
    ('F4 non meublé Beauséjour 900', {'has':['T/F4','Non meublé','Beauséjour','900 € ?'], 'not_has':['Budget 900 € à préciser'], 'q':'F4 non meublé Beauséjour 900'}),
    ('F4 non meublé Bretagne 900', {'has':['T/F4','Non meublé','Bretagne','900 € ?'], 'not_has':['Budget 900 € à préciser'], 'q':'F4 non meublé Bretagne 900'}),
    ('T4 non meublé Grande Montée moins 1300', {'has':['T/F4','Non meublé','Grande Montée','≤ 1300 €'], 'not_has':['900 € ?'], 'q':'T4 non meublé Grande Montée moins 1300'}),
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':390,'height':844})
        console=[]
        page.on('console', lambda m: console.append(f'{m.type}: {m.text}'))
        await page.goto(URL, wait_until='networkidle')
        results=[]
        for q,expect in CASES:
            await page.fill('#q', q)
            await page.wait_for_timeout(600)
            data = await page.evaluate("""() => ({
                q: document.querySelector('#q').value,
                chips: [...document.querySelectorAll('#nlChips .nlchip')].map(x=>x.innerText.trim().split('\\n').join(' ')),
                understood: document.querySelector('#understood').textContent,
                summary: document.querySelector('#summary').textContent,
                horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
                housingPills: [...document.querySelectorAll('.housingPill')].slice(0,10).map(x=>x.textContent.trim()),
                cards: document.querySelectorAll('.card').length
            })""")
            ok=True; errs=[]
            joined=' | '.join(data['chips'])
            if data['q'] != expect['q']:
                ok=False; errs.append(f'q changed: {data["q"]!r}')
            for s in expect['has']:
                if s not in joined:
                    ok=False; errs.append(f'missing chip {s!r} in {joined!r}')
            for s in expect['not_has']:
                if s in joined:
                    ok=False; errs.append(f'unwanted duplicate {s!r}')
            if data['horizontalOverflow']:
                ok=False; errs.append('horizontal overflow')
            if data['cards'] > 0 and not data['housingPills']:
                ok=False; errs.append('no housing detail pills visible on non-empty results')
            results.append({'input':q,'ok':ok,'errors':errs,'data':data})
        # Price operator preserves initial terms
        await page.fill('#q', 'F4 non meublé Beauséjour 900')
        await page.wait_for_timeout(500)
        await page.click('[data-price-op="max"]')
        await page.wait_for_timeout(500)
        op = await page.evaluate("""() => ({q:document.querySelector('#q').value, chips:[...document.querySelectorAll('#nlChips .nlchip')].map(x=>x.innerText.trim().split('\\n').join(' ')), summary:document.querySelector('#summary').textContent})""")
        op_ok = op['q'] == 'F4 non meublé Beauséjour moins 900' and 'T/F4 ×' in op['chips'] and 'Non meublé ×' in op['chips'] and 'Beauséjour ×' in op['chips'] and any('≤ 900 €' in c for c in op['chips'])
        # Modal has housing box
        await page.fill('#q', '')
        await page.wait_for_timeout(500)
        first = page.locator('.card button[data-open]').first
        await first.click()
        await page.wait_for_timeout(300)
        modal = await page.evaluate("""() => ({open:document.querySelector('#modal')?.classList.contains('open'), housing:!!document.querySelector('.housingDetailBox'), text:document.querySelector('.housingDetailBox')?.innerText || ''})""")
        await browser.close()
    out={'ok': all(r['ok'] for r in results) and op_ok and modal['open'] and modal['housing'] and not any('error' in c.lower() for c in console), 'cases':results, 'operator':{'ok':op_ok,'data':op}, 'modal':modal, 'console':console[-20:]}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if out['ok'] else 1)

if __name__ == '__main__':
    asyncio.run(main())
