#!/usr/bin/env python3
"""
SeLoger La Réunion — Multi-page scraper.
Utilise JS clicks pour la pagination (boutons React sans href).
267 annonces disponibles, 30/page.
"""
import json, re, time, os, socket, sys
from pathlib import Path


# Playwright must come from the interpreter environment selected by the
# pipeline (normally PROJECT/.venv). Never prepend a user-site from another
# Python minor version: that loads incompatible native wheels such as
# greenlet._greenlet and breaks the collector.
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/data/.cache/ms-playwright')

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.seloger_collection_manifest import (
    build_seloger_manifest, evaluate_seloger_collection, extract_reported_total,
)

from playwright.sync_api import sync_playwright
from datetime import datetime, timezone

def default_cdp_url():
    # The CDP proxy rewrites websocket URLs using its public host. Chrome rejects
    # non-localhost hostnames in WebSocket Host headers, while Docker bridge IPs
    # are accepted. Resolve the stable container DNS name to its current IP at
    # runtime instead of hardcoding volatile 172.16.x.y addresses.
    host = os.getenv('SELOGER_CDP_HOST', 'chromium-cdp')
    port = os.getenv('SELOGER_CDP_PORT', '9223')
    try:
        host = socket.gethostbyname(host)
    except Exception:
        pass
    return f'http://{host}:{port}'

CDP_IP = os.getenv('SELOGER_CDP_URL') or default_cdp_url()
OUT_DIR = '/opt/data/artifacts/realestate/'
os.makedirs(OUT_DIR, exist_ok=True)

BASE_URL = 'https://www.seloger.com/classified-search?distributionTypes=Rent&estateTypes=House,Apartment&locations=AD04RE1'
PAGE_SIZE = 30
MAX_PAGES = int(os.getenv('SELOGER_MAX_PAGES', '100'))  # safety cap, not completion proof

def force_dismiss_consent(page):
    """Agressively remove all consent overlays."""
    page.evaluate(r"""
        () => {
            // Remove all known consent elements
            ['usercentrics-root', 'didomi-notice', 'onetrust-consent-sdk'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.remove();
            });
            // Remove by class patterns
            document.querySelectorAll('[class*="consent"], [class*="cookie"], [id*="consent"], [id*="cookie"]').forEach(el => el.remove());
            // Remove any element with z-index > 9000 that's a div (likely overlay)
            document.querySelectorAll('div, aside').forEach(el => {
                const z = window.getComputedStyle(el).zIndex;
                if (parseInt(z) > 9000 && el.id && el.id.includes('consent')) el.remove();
            });
        }
    """)

def click_page_button(page, page_num):
    """Click pagination button for given page number via JS."""
    result = page.evaluate(f"""
        (pageNum) => {{
            // Find pagination buttons
            const btns = Array.from(document.querySelectorAll('button[aria-label]'));
            const target = btns.find(b => {{
                const label = b.getAttribute('aria-label') || '';
                return label.includes('page ' + pageNum) || label.includes('à la page ' + pageNum);
            }});
            if (target) {{
                target.click();
                return {{clicked: true, label: target.getAttribute('aria-label')}};
            }}
            return {{clicked: false, available: btns.map(b => b.getAttribute('aria-label')).filter(l => l && l.includes('page')).slice(0,10)}};
        }}
    """, page_num)
    return result

def parse_price(text):
    """Extract rental price from SeLoger card text.

    Cards often start with a gallery counter like ``1 / 13`` before the price.
    A naive regex on the flattened text can parse ``1 / 13\n960 €`` as 13960,
    or ``1 345 €`` as 345. Prefer the actual line containing ``€`` and keep
    French thousands separators (space, NBSP, narrow NBSP).
    """
    lines = [ln.strip() for ln in text.splitlines() if '€' in ln]
    candidates = lines if lines else [text]
    for line in candidates:
        # Keep the full grouped number immediately before the euro sign.
        # Examples: "960 € /mois", "1 345 € /mois", "2 200 € /mois".
        m = re.search(r'(?<![\d/])([0-9][0-9\s\xa0\u202f]{1,8})\s*€', line)
        if not m:
            m = re.search(r'(?<![\d/])(\d{3,5})\s*€', line)
        if not m:
            continue
        clean = re.sub(r'[\s\xa0\u202f]', '', m.group(1))
        try:
            v = int(clean)
            if 200 < v < 15000:
                return v
        except Exception:
            pass
    return None

def parse_surface(text):
    m = re.search(r'([\d,\.]+)\s*m[²2]', text)
    if m:
        try:
            return float(m.group(1).replace(',', '.'))
        except:
            pass
    return None

def extract_cards(page):
    return page.evaluate(r"""
        () => {
            const cards = document.querySelectorAll('[id^="classified-card-"]');
            const results = [];
            const bad = /logo|avatar|sprite|placeholder|blank|default|data:image[/]svg/i;
            const pickFromSrcset = (srcset) => {
                if (!srcset) return null;
                const parts = srcset.split(',').map(s => s.trim().split(/\s+/)[0]).filter(Boolean);
                return parts.find(u => !bad.test(u)) || null;
            };
            const absolute = (u) => {
                if (!u || bad.test(u)) return null;
                try { return new URL(u, location.href).href; } catch(e) { return null; }
            };
            const imageFromCard = (card) => {
                const attrs = ['src', 'data-src', 'data-lazy-src', 'data-original', 'data-url'];
                for (const img of Array.from(card.querySelectorAll('img, source'))) {
                    const ss = pickFromSrcset(img.getAttribute('srcset') || img.getAttribute('data-srcset'));
                    const ssAbs = absolute(ss);
                    if (ssAbs) return ssAbs;
                    for (const attr of attrs) {
                        const val = absolute(img.getAttribute(attr));
                        if (val) return val;
                    }
                }
                for (const el of Array.from(card.querySelectorAll('[style*="background"]'))) {
                    const style = el.getAttribute('style') || '';
                    const m = style.match(/url\(["']?([^"')]+)["']?\)/i);
                    const val = m ? absolute(m[1]) : null;
                    if (val) return val;
                }
                const html = card.innerHTML || '';
                const imageRegex = /https?:[^"'<>\\]+\.(?:jpg|jpeg|png|webp)(?:\?[^"'<>\\]*)?/i;
                const m = html.match(imageRegex);
                if (m) return absolute(m[0].replaceAll('\\\\/', '/'));
                return null;
            };
            cards.forEach(card => {
                const id = card.id.replace('classified-card-', '');
                const text = card.innerText || '';
                const links = Array.from(card.querySelectorAll('a[href*="seloger.com"]'));
                const link = links.length > 0 ? links[0] : null;
                results.push({id, text, url: link ? link.href : null, image_url: imageFromCard(card)});
            });
            return results;
        }
    """)

def normalize_seloger_image_url(u):
    if not u:
        return None
    # SeLoger cards expose small thumbnails with h=50. The same signed URL
    # accepts larger heights; h=800 was HTTP-validated and is usable for UI.
    if 'mms.seloger.com' in u:
        if re.search(r'([?&])h=\d+', u):
            return re.sub(r'([?&])h=\d+', r'\1h=800', u)
        return u + ('&' if '?' in u else '?') + 'h=800'
    return u

def parse_card(card):
    text = card['text']
    t = 'Autre'
    for typ in ['Studio', 'Appartement', 'Maison', 'Villa', 'Duplex', 'Loft', 'Local']:
        if typ.lower() in text.lower():
            t = typ
            break
    rooms_m = re.search(r'(\d+)\s*pièce', text, re.I)
    beds_m = re.search(r'(\d+)\s*chambre', text, re.I)
    return {
        'id': card['id'],
        'url': card['url'],
        'image_url': normalize_seloger_image_url(card.get('image_url')),
        'prix': parse_price(text),
        'surface': parse_surface(text),
        'nb_pieces': int(rooms_m.group(1)) if rooms_m else None,
        'nb_chambres': int(beds_m.group(1)) if beds_m else None,
        'type_bien': t,
        'source': 'seloger_cdp',
        'date_collecte': datetime.now().isoformat(),
    }

def collect_all_pages():
    print('=== SeLoger La Réunion — Multi-Page Scraper ===')
    print(f'Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    all_annonces = {}
    page_sizes = []
    pages_attempted = 0
    pages_succeeded = 0
    reported_total = None
    terminal_reason = 'page_cap'
    collection_error = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_IP)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()

            pages_attempted += 1
            page.goto(BASE_URL, wait_until='domcontentloaded', timeout=30000)
            force_dismiss_consent(page)
            cards = extract_cards(page)
            for card in cards:
                p_data = parse_card(card)
                all_annonces[p_data['id']] = p_data
            pages_succeeded += 1
            page_sizes.append(len(cards))
            total_text = page.evaluate(
                "() => { const h1 = document.querySelector('h1'); return h1 ? h1.innerText : ''; }"
            )
            reported_total = extract_reported_total(total_text)
            print(f'Total annoncé: {total_text!r} -> {reported_total}')
            print(f'Page 1: {len(cards)} cards | Total unique: {len(all_annonces)}')

            if reported_total is not None and len(all_annonces) >= reported_total:
                terminal_reason = 'reported_total_reached'
            elif len(cards) == 0:
                terminal_reason = 'empty_page'
            elif len(cards) < PAGE_SIZE:
                terminal_reason = 'short_page'
            else:
                for page_num in range(2, MAX_PAGES + 1):
                    pages_attempted += 1
                    force_dismiss_consent(page)
                    click_result = click_page_button(page, page_num)
                    print(f'Page {page_num} click: {click_result}')
                    if not click_result.get('clicked'):
                        terminal_reason = 'no_next_page_button'
                        break

                    time.sleep(4)
                    force_dismiss_consent(page)
                    cards = extract_cards(page)
                    pages_succeeded += 1
                    page_sizes.append(len(cards))
                    new_count = 0
                    for card in cards:
                        p_data = parse_card(card)
                        if p_data['id'] not in all_annonces:
                            all_annonces[p_data['id']] = p_data
                            new_count += 1
                    print(
                        f'Page {page_num}: {len(cards)} cards | '
                        f'{new_count} nouveaux | Total unique: {len(all_annonces)}'
                    )

                    if reported_total is not None and len(all_annonces) >= reported_total:
                        terminal_reason = 'reported_total_reached'
                        break
                    if len(cards) == 0:
                        terminal_reason = 'empty_page'
                        break
                    if new_count == 0:
                        terminal_reason = 'repeated_page'
                        break
                    if len(cards) < PAGE_SIZE:
                        terminal_reason = 'short_page'
                        break
                    time.sleep(1.5)
            page.close()
    except Exception as exc:
        collection_error = f'{type(exc).__name__}: {exc}'
        terminal_reason = 'page_error'
        print(f'COLLECTION_ERROR: {collection_error}')

    evidence = evaluate_seloger_collection(
        page_sizes=page_sizes,
        unique_ids=len(all_annonces),
        reported_total=reported_total,
        terminal_reason=terminal_reason,
        pages_attempted=pages_attempted,
        pages_succeeded=pages_succeeded,
        error=collection_error,
        page_size=PAGE_SIZE,
    )
    return all_annonces, evidence


def main():
    refresh_run_dir = os.getenv('IMMO_REFRESH_RUN_DIR', '').strip()
    run_id = (
        os.getenv('IMMO_RUN_ID', '').strip()
        or (Path(refresh_run_dir).name if refresh_run_dir else datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    )
    all_annonces, evidence = collect_all_pages()
    annonces = list(all_annonces.values())
    with_price = [a for a in annonces if a['prix']]
    types = {}
    for a in annonces:
        t = a.get('type_bien', 'Autre')
        types[t] = types.get(t, 0) + 1
    print('\n=== RÉSULTAT FINAL ===')
    print(f'Total: {len(annonces)} annonces SeLoger La Réunion')
    print(f'Avec prix: {len(with_price)}')
    print(f'Types: {types}')
    if with_price:
        prices = sorted(a['prix'] for a in with_price)
        print(f'Prix: {prices[0]}€ min / {prices[-1]}€ max / {int(sum(prices)/len(prices))}€ moy')
    print('\nSample:')
    for a in annonces[:8]:
        print(f'  {a["type_bien"]} {a["nb_pieces"]}p {a["surface"]}m² — {a["prix"]}€')

    out_path = Path(OUT_DIR) / 'seloger_multipage_results.json'
    out_path.write_text(
        json.dumps(
            {
                'date': datetime.now(timezone.utc).isoformat(),
                'total': len(annonces),
                'with_price': len(with_price),
                'types': types,
                'collection': {
                    'page_sizes': list(evidence.page_sizes),
                    'reported_total': evidence.reported_total,
                    'terminal_reason': evidence.terminal_reason,
                    'pages_attempted': evidence.pages_attempted,
                    'pages_succeeded': evidence.pages_succeeded,
                    'complete': evidence.complete,
                    'truncation_signals': evidence.truncation_signals,
                    'error': evidence.error,
                },
                'annonces': annonces,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding='utf-8',
    )
    print(f'\nSaved: {out_path}')
    manifest = build_seloger_manifest(
        evidence,
        run_id=run_id,
        artifact_path=out_path,
        normalized_ids=set(all_annonces),
        event_statuses=['seen'] * len(annonces),
    )
    manifest_path = Path(OUT_DIR) / 'seloger_collection_manifest.provisional.json'
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    print(f'Manifest: {manifest_path} [{manifest["status"]}]')
    smoke_summary = {
        'ok': manifest['status'] == 'complete',
        'source': 'seloger_cdp_reunion_rentals',
        'total': len(annonces),
        'with_price': len(with_price),
        'types': types,
        'artifact': str(out_path),
        "source_manifest": str(manifest_path),
        'manifest_status': manifest['status'],
        'terminal_reason': manifest['terminal_reason'],
        'page_sizes': manifest['page_sizes'],
        'sample': annonces[:3],
    }
    print(json.dumps(smoke_summary, ensure_ascii=False, default=str))
    return 0 if manifest["status"] == "complete" else 2

if __name__ == '__main__':
    raise SystemExit(main())
