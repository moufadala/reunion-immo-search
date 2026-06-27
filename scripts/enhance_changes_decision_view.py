#!/usr/bin/env python3
"""Enhance changes.html with a decision-oriented summary.

Non-destructive injection: source changes.json remains the truth; this only makes the public page answer
"what deserves attention today?" before long raw lists.
"""
from __future__ import annotations
import json, re, html
import argparse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT / 'artifacts' / 'app'
APP = DEFAULT_APP
HTML = APP / 'changes.html'
JSONP = APP / 'changes.json'


def eur(v: Any) -> str:
    try:
        return f"{int(float(v)):,}".replace(',', ' ') + ' €'
    except Exception:
        return 'prix n.c.'


def item_title(c: dict[str, Any]) -> str:
    it = c.get('item') or c.get('listing') or c
    return str(it.get('title') or it.get('name') or c.get('title') or 'Annonce').strip()


def item_loc(c: dict[str, Any]) -> str:
    it = c.get('item') or c.get('listing') or c
    return ' · '.join(str(it.get(k) or '').strip() for k in ('city','district','region') if it.get(k)) or str(it.get('location') or '').strip()


def main() -> int:
    global APP, HTML, JSONP
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default=str(DEFAULT_APP))
    args = ap.parse_args()
    APP = Path(args.app)
    HTML = APP / 'changes.html'
    JSONP = APP / 'changes.json'
    if not HTML.exists() or not JSONP.exists():
        raise FileNotFoundError('changes.html/json missing')
    data = json.loads(JSONP.read_text())
    changes = data.get('changes') or []
    price_down = []
    reappeared = []
    disappeared = []
    price_up = []
    for c in changes:
        ev = c.get('event_type')
        direction = c.get('direction') or ''
        old = c.get('old_value') or c.get('old_price')
        new = c.get('new_value') or c.get('new_price')
        if ev == 'price_changed':
            try:
                delta = float(new) - float(old)
            except Exception:
                delta = 0
            row = {**c, '_delta': delta, '_abs_delta': abs(delta)}
            (price_down if delta < 0 or direction == 'down' else price_up).append(row)
        elif ev == 'reappeared':
            reappeared.append(c)
        elif ev == 'disappeared':
            disappeared.append(c)
    price_down.sort(key=lambda x: x.get('_abs_delta') or 0, reverse=True)

    def card(c: dict[str, Any], kind: str) -> str:
        old = c.get('old_value') or c.get('old_price')
        new = c.get('new_value') or c.get('new_price')
        delta = c.get('_delta')
        delta_txt = f" ({delta:+.0f} €)" if isinstance(delta, (int,float)) else ''
        title = html.escape(item_title(c)[:120])
        loc = html.escape(item_loc(c))
        url = html.escape(str((c.get('item') or c.get('listing') or c).get('url') or '#'))
        if kind == 'price_down':
            main = f"<b>{eur(old)} → {eur(new)}{html.escape(delta_txt)}</b>"
            label = 'Baisse à regarder'
        elif kind == 'reappeared':
            main = '<b>Annonce revenue en ligne</b>'
            label = 'Réapparition'
        else:
            main = '<b>Annonce disparue</b>'
            label = 'Disparue'
        return f"<article class='decision-card decision-{kind}'><span>{label}</span><h3>{title}</h3><p>{loc}</p><p>{main}</p><a href='{url}' target='_blank' rel='noopener'>Voir la source</a></article>"

    html_block = f"""
<section id="decisionBrief" class="decision-brief">
  <div class="decision-head">
    <p class="eyebrow">Synthèse décision</p>
    <h2>À regarder en priorité</h2>
    <p>Les baisses de prix et réapparitions sont mises devant les disparues pour éviter de noyer les signaux utiles.</p>
  </div>
  <div class="decision-stats">
    <b>{len(price_down)}</b><span>baisses de prix</span>
    <b>{len(reappeared)}</b><span>réapparitions</span>
    <b>{len(disappeared)}</b><span>disparues</span>
  </div>
  <div class="decision-grid">
    {''.join(card(c, 'price_down') for c in price_down[:8])}
    {''.join(card(c, 'reappeared') for c in reappeared[:4])}
  </div>
</section>
"""
    css = """
<style id="decisionBriefCss">
.decision-brief{margin:18px 0 22px;padding:18px;border:1px solid #e6dfd3;border-radius:20px;background:#fffdf8;box-shadow:0 14px 38px rgba(55,45,30,.08)}
.decision-head h2{margin:.1rem 0;font-size:clamp(24px,4vw,38px);letter-spacing:-.04em}.decision-head p{color:#6f6a61}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.12em;font-weight:900;color:#0f766e}.decision-stats{display:grid;grid-template-columns:repeat(3,auto 1fr);gap:6px 10px;align-items:baseline;margin:12px 0}.decision-stats b{font-size:28px;color:#134e4a}.decision-stats span{color:#6f6a61}.decision-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}.decision-card{border:1px solid #e6dfd3;border-radius:16px;padding:13px;background:white}.decision-card span{font-size:12px;font-weight:900;color:#0f766e}.decision-card h3{font-size:15px;margin:6px 0;line-height:1.25}.decision-card p{font-size:13px;color:#6f6a61;margin:5px 0}.decision-card a{font-size:13px;font-weight:800;color:#0f766e}.decision-price_down{border-color:#bbf7d0;background:#f6fff8}@media(max-width:580px){.decision-stats{grid-template-columns:auto 1fr}.decision-brief{margin-left:0;margin-right:0;padding:14px}.decision-grid{grid-template-columns:1fr}}
</style>
"""
    doc = HTML.read_text()
    doc = re.sub(r'<style id="decisionBriefCss">.*?</style>\s*', '', doc, flags=re.S)
    doc = re.sub(r'<section id="decisionBrief".*?</section>\s*', '', doc, flags=re.S)
    doc = doc.replace('</head>', css + '\n</head>') if '</head>' in doc else css + doc
    if '<main' in doc:
        doc = re.sub(r'(<main[^>]*>)', r'\1\n' + html_block, doc, count=1)
    else:
        doc = html_block + doc
    HTML.write_text(doc)
    print(json.dumps({'ok': True, 'price_down': len(price_down), 'reappeared': len(reappeared), 'disappeared': len(disappeared), 'html': str(HTML)}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
