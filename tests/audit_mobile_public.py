#!/usr/bin/env python3
"""Mobile UX smoke audit for the public Immo RUN portal."""
from __future__ import annotations
import json, os
from playwright.sync_api import sync_playwright
BASE=os.environ.get('IMMO_PUBLIC_BASE','https://immo.148.230.103.174.sslip.io').rstrip('/')
VIEWPORTS=[{'width':360,'height':740},{'width':390,'height':844},{'width':768,'height':1024}]
errors=[]; evidence=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    for vp in VIEWPORTS:
        page=browser.new_page(viewport=vp)
        page.goto(BASE+'/?rev=mobile-audit', wait_until='networkidle', timeout=45000)
        page.wait_for_timeout(1100)
        initial=page.evaluate("""() => {
          const rect = el => el ? el.getBoundingClientRect().toJSON() : null;
          return {
            width: window.innerWidth,
            scrollWidth: document.documentElement.scrollWidth,
            qBox: rect(document.querySelector('#q')),
            filterToggle: rect(document.querySelector('#filterToggle')),
            filterToggleText: document.querySelector('#filterToggle')?.innerText || '',
            filterPanel: rect(document.querySelector('#filtersPanel')),
            resetHidden: document.querySelector('#resetBtn')?.classList.contains('hidden') || getComputedStyle(document.querySelector('#resetBtn')).display === 'none',
            stats: [...document.querySelectorAll('.stat')].map(x=>rect(x)),
            cards: document.querySelectorAll('.card').length,
            firstCard: rect(document.querySelector('.card')),
            firstPhoto: rect(document.querySelector('.card .photo')),
            cardActions: [...document.querySelectorAll('.card:first-child .actions a,.card:first-child .actions button')].map(x=>({text:x.innerText, title:x.title, rect:rect(x)})),
            fav: rect(document.querySelector('.card:first-child [data-fav]')),
            nlChipsBox: rect(document.querySelector('#nlChips')),
            understoodBox: rect(document.querySelector('#understood'))
          }
        }""")
        if initial['scrollWidth'] > initial['width'] + 2: errors.append(f"initial overflow viewport {vp}: {initial['scrollWidth']} > {initial['width']}")
        if not initial['qBox'] or initial['qBox']['top'] > 120: errors.append(f"viewport {vp}: recherche pas visible en haut {initial['qBox']}")
        if vp['width'] <= 580:
            ft=initial['filterToggle']
            if not ft or ft['height'] < 44: errors.append(f"viewport {vp}: bouton Filtres absent/trop petit {ft}")
            if not initial['resetHidden']: errors.append(f"viewport {vp}: reset visible sans filtre actif")
            if len(initial['stats']) < 4: errors.append(f"viewport {vp}: métriques compactes absentes {initial['stats']}")
            if initial['firstCard'] and initial['firstCard']['height'] > 250: errors.append(f"viewport {vp}: card mobile trop haute {initial['firstCard']}")
            if not initial['firstPhoto'] or initial['firstPhoto']['width'] > 130: errors.append(f"viewport {vp}: card pas assez compacte/image-first {initial['firstPhoto']}")
            action_text=' '.join((a['text'] or a['title'] or '') for a in initial['cardActions'])
            for expected in ['Source','Analyse','Masquer']:
                if expected not in action_text: errors.append(f"viewport {vp}: action {expected} absente {initial['cardActions']}")
            if not initial['fav'] or initial['fav']['height'] < 38: errors.append(f"viewport {vp}: favori absent/trop petit {initial['fav']}")
            for a in initial['cardActions']:
                r=a['rect']
                if r and (r['height'] < 38 or r['left'] < -1 or r['right'] > initial['width']+1):
                    errors.append(f"viewport {vp}: action card peu accessible {a}")
            page.locator('#filterToggle').click()
            page.wait_for_timeout(250)
            sheet=page.evaluate("""() => {
              const panel=document.querySelector('#filtersPanel'), scrim=document.querySelector('#filterScrim'), toggle=document.querySelector('#filterToggle');
              const r=panel?.getBoundingClientRect();
              return {
                open: document.body.classList.contains('filtersOpen'),
                expanded: toggle?.getAttribute('aria-expanded'),
                scrimHidden: scrim?.hidden,
                panel: r ? r.toJSON() : null,
                controls: [...document.querySelectorAll('#filtersPanel select,#filtersPanel input,#closeFilters')].map(x=>x.getBoundingClientRect().toJSON())
              }
            }""")
            if not sheet['open'] or sheet['expanded'] != 'true' or sheet['scrimHidden']:
                errors.append(f"viewport {vp}: panneau filtres non ouvert {sheet}")
            if not sheet['panel'] or sheet['panel']['bottom'] < vp['height']-2 or sheet['panel']['height'] < 220:
                errors.append(f"viewport {vp}: panneau filtres pas bottom sheet {sheet['panel']}")
            for r in sheet['controls']:
                if r['height'] < 40 or r['left'] < -1 or r['right'] > initial['width']+1:
                    errors.append(f"viewport {vp}: contrôle filtre mobile fragile {r}")
            page.locator('#closeFilters').click()
            page.wait_for_timeout(150)
        page.fill('#q','900')
        page.wait_for_timeout(550)
        data=page.evaluate("""() => ({
          width: window.innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          qBox: document.querySelector('#q')?.getBoundingClientRect().toJSON(),
          filterToggleText: document.querySelector('#filterToggle')?.innerText || '',
          resetHidden: document.querySelector('#resetBtn')?.classList.contains('hidden') || getComputedStyle(document.querySelector('#resetBtn')).display === 'none',
          nlChips: [...document.querySelectorAll('#nlChips .nlchip')].map(x=>x.innerText.trim()),
          priceButtons: [...document.querySelectorAll('[data-price-op]')].map(b=>({op:b.dataset.priceOp, rect:b.getBoundingClientRect().toJSON(), text:b.innerText})),
          cards: document.querySelectorAll('.card').length,
          summary: document.querySelector('#summary')?.textContent || '',
          understood: document.querySelector('#understood')?.textContent || '',
          loadMore: (() => { const b=document.querySelector('#loadMore'); if(!b)return null; const r=b.getBoundingClientRect(); return {hidden:b.classList.contains('hidden'), width:r.width, height:r.height, text:b.innerText}; })()
        })""")
        if data['scrollWidth'] > data['width'] + 2: errors.append(f"overflow viewport {vp}: {data['scrollWidth']} > {data['width']}")
        if vp['width'] <= 580 and data['resetHidden']: errors.append(f"viewport {vp}: reset reste masqué avec filtre actif")
        if vp['width'] <= 580 and 'Filtres' not in data['filterToggleText']: errors.append(f"viewport {vp}: texte bouton filtres instable {data['filterToggleText']!r}")
        if not any('900' in c for c in data['nlChips']): errors.append(f"viewport {vp}: chip actif budget absent {data['nlChips']}")
        if len(data['priceButtons']) < 4: errors.append(f"viewport {vp}: boutons budget incomplets {data['priceButtons']}")
        for b in data['priceButtons']:
            r=b['rect']
            if r['width'] < 24 or r['height'] < 24: errors.append(f"viewport {vp}: bouton {b['op']} trop petit {r}")
            if r['left'] < -1 or r['right'] > data['width']+1: errors.append(f"viewport {vp}: bouton {b['op']} hors écran {r}")
        if data['loadMore'] and not data['loadMore']['hidden'] and data['loadMore']['height'] < 44:
            errors.append(f"viewport {vp}: CTA afficher plus trop petit {data['loadMore']}")
        open_button = page.locator('[data-open]').first
        if open_button.count():
            open_button.click()
            page.wait_for_timeout(250)
            modal = page.evaluate("""() => ({
              open: document.querySelector('#modal')?.classList.contains('open') || false,
              hidden: document.querySelector('#modal')?.hidden,
              text: document.querySelector('#modal')?.innerText || ''
            })""")
            if not modal['open'] or modal['hidden'] or ('Fermer' not in modal['text'] or 'Ouvrir la source' not in modal['text']):
                errors.append(f"viewport {vp}: Analyse détaillée n'ouvre pas une modale exploitable {modal}")
            page.keyboard.press('Escape')
        page.click('[data-price-op="max"]')
        page.wait_for_timeout(300)
        q=page.locator('#q').input_value()
        understood=page.locator('#understood').inner_text()
        if 'moins 900' not in q or '≤ 900' not in understood: errors.append(f"viewport {vp}: clic ≤ non synchronisé q={q!r} understood={understood!r}")
        evidence.append(data)
        page.close()
    browser.close()
print(json.dumps({'ok':not errors,'base':BASE,'errors':errors,'viewports':VIEWPORTS,'evidence_sample':evidence}, ensure_ascii=False, indent=2))
if errors: raise SystemExit(1)
print('MOBILE_PUBLIC_AUDIT PASS')
