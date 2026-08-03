#!/usr/bin/env python3
"""P0 product polish for the public RUN immo portal.

Non destructive: enriches the static app with explicit freshness/status signals,
detail-modal evidence, duplicate/history data, and a small changes-page safety patch.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

P0_CSS = r'''
/* P0 product polish: status, detail evidence, mobile clarity */
.statusStrip{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 0}.statusPill{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:5px 8px;font-size:11px;font-weight:850;border:1px solid var(--line);background:#fff;color:var(--muted)}.statusPill.ok{background:#edfdf7;border-color:#bce9d5;color:#087f5b}.statusPill.warn{background:#fff8e6;border-color:#f4d38d;color:#8a4b00}.statusPill.bad{background:#fff1f0;border-color:#ffccc7;color:#b42318}.statusPill.info{background:#eef4ff;border-color:#d7e5ff;color:#385170}.detailEvidence{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:12px}.evidenceBox{border:1px solid var(--line);background:#fffdf8;border-radius:14px;padding:11px}.evidenceBox h4{margin:0 0 6px;font-size:13px}.evidenceBox ul{margin:0;padding-left:17px;color:var(--muted);font-size:12px;line-height:1.45}.evidenceBox p{margin:0;color:var(--muted);font-size:12px;line-height:1.45}.sourceHealthLine{margin-top:10px;font-size:12px;color:var(--muted)}.detailPrimaryActions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.detailPrimaryActions a,.detailPrimaryActions button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:9px 12px;text-decoration:none;font-weight:850;font-size:13px}.detailPrimaryActions a.primary{background:var(--brand);border-color:var(--brand);color:#fff}.changesHelp{margin:0 0 14px;border:1px solid #d7e5ff;background:#eef4ff;color:#274060;border-radius:14px;padding:10px 12px;font-size:13px;line-height:1.45}.change-card[hidden]{display:none!important}@media(max-width:580px){.statusStrip{gap:5px}.statusPill{font-size:10.5px;padding:5px 7px}.detailEvidence{grid-template-columns:1fr}.detailPrimaryActions a,.detailPrimaryActions button{min-height:44px;display:inline-flex;align-items:center}.change-card[hidden]{display:none!important}}
'''

P0_JS = r'''
<script id="p0ProductPolish">
(function(){
function escP0(s){return (s??'').toString().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function attrP0(s){return escP0(s).replace(/`/g,'&#96;');}
function fmtDateP0(s){if(!s)return 'date n.c.'; try{return new Date(s).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'short'});}catch(e){return s;}}
function fmtAgeP0(h){if(h==null || Number.isNaN(Number(h))) return 'âge n.c.'; h=Number(h); if(h<1)return 'vu il y a <1h'; if(h<48)return 'vu il y a '+Math.round(h)+'h'; return 'vu il y a '+Math.round(h/24)+'j';}
function statusClassP0(s){return s==='fresh'||s==='active'||s==='ok'?'ok':(s==='stale'||s==='warning'||s==='partial'?'warn':'info');}
function availabilityP0(x){const p=x._p0||{}; return p.availability||{};}
function statusHtmlP0(x){const p=x._p0||{}, av=availabilityP0(x), hist=p.history||{}, dup=p.duplicates||{}, sh=p.source_health||{}; const out=[]; out.push(`<span class="statusPill ${statusClassP0(av.status)}" title="Dernière vue: ${escP0(av.last_seen_at||'n.c.')}">${escP0(av.label||'Active')} · ${escP0(fmtAgeP0(av.age_hours))}</span>`); if(sh.status) out.push(`<span class="statusPill ${statusClassP0(sh.status)}" title="${escP0(sh.reason||'')}">source ${escP0(sh.status)}</span>`); if(hist.price_changes) out.push(`<span class="statusPill info">${hist.price_changes} prix</span>`); if(hist.disappeared) out.push(`<span class="statusPill warn">a déjà disparu</span>`); if(dup.count) out.push(`<span class="statusPill info">${dup.count+1} sources</span>`); if((x.local_image_urls||[]).length>1) out.push(`<span class="statusPill info">${x.local_image_urls.length} photos</span>`); return `<div class="statusStrip">${out.join('')}</div>`;}
function addCardStatusP0(){document.querySelectorAll('.card').forEach(card=>{if(card.querySelector('.statusStrip'))return; const id=card.dataset.id; const x=(window.all||[]).find(i=>String(i.id)===String(id)); const body=card.querySelector('.body'); const actions=card.querySelector('.actions'); if(!x||!body||!actions)return; const wrap=document.createElement('div'); wrap.innerHTML=statusHtmlP0(x); body.insertBefore(wrap.firstElementChild, actions);});}
function buildHistoryP0(x){const h=(x._p0||{}).history||{}; const events=h.events||[]; if(!events.length)return '<p>Aucun changement historique utile exposé pour cette annonce.</p>'; return '<ul>'+events.slice(0,5).map(e=>{let txt=e.event_type==='price_changed'?`Prix: ${e.old_value??'?'} → ${e.new_value??'?'} €`:(e.event_type==='disappeared'?'Annonce disparue':(e.event_type==='reappeared'?'Annonce réapparue':e.event_type)); return `<li>${escP0(fmtDateP0(e.event_at))} · ${escP0(txt)}</li>`;}).join('')+'</ul>';}
function buildDuplicateP0(x){const d=(x._p0||{}).duplicates||{}; const links=d.links||[]; if(!links.length)return '<p>Aucun doublon fort exposé. Ça ne prouve pas qu’il n’y en a pas, juste qu’aucun groupe public n’est associé.</p>'; return '<ul>'+links.slice(0,6).map(l=>`<li>${escP0(l.source||'source')} · ${escP0(l.price||'?')} € · ${escP0(l.surface||'?')} m²</li>`).join('')+'</ul>';}
function detailEvidenceP0(x){const p=x._p0||{}, av=p.availability||{}, sh=p.source_health||{}, iq=x.image_quality||{}, gq=x.geo_quality||{}; const sourceUrl=attrP0(x.url||'#'); return `<div class="sourceHealthLine">Disponibilité: <strong>${escP0(av.label||'active')}</strong> (${escP0(fmtAgeP0(av.age_hours))}) · Source ${escP0(x.source||'n.c.')} : ${escP0(sh.reason||sh.status||'état n.c.')}</div><div class="detailEvidence"><div class="evidenceBox"><h4>Historique annonce</h4>${buildHistoryP0(x)}</div><div class="evidenceBox"><h4>Doublons / multi-sources</h4>${buildDuplicateP0(x)}</div><div class="evidenceBox"><h4>Qualité données</h4><ul><li>Localisation: ${escP0(gq.level||x.location_intelligence?.quality||'source')}</li><li>Photos locales valides: ${escP0(iq.valid_local_count??0)}/${escP0(iq.local_count??0)}</li><li>Description: ${escP0(x.description_status||'source/export')}</li></ul></div><div class="evidenceBox"><h4>À vérifier avant contact</h4><p>Le portail consolide des sources scrapées. Le prix, la disponibilité et l’adresse exacte doivent être confirmés sur l’annonce source ou auprès de l’agence.</p></div></div><div class="detailPrimaryActions"><a class="primary" href="${sourceUrl}" target="_blank" rel="noreferrer">Ouvrir la source</a>${x.map_url?`<a href="${attrP0(x.map_url)}" target="_blank" rel="noreferrer">Carte</a>`:''}</div>`;}
const previousRenderP0 = typeof render==='function' ? render : null;
if(previousRenderP0){ render=function(res){ previousRenderP0(res); addCardStatusP0(); }; }
const previousOpenDetailP0 = typeof openDetail==='function' ? openDetail : null;
if(previousOpenDetailP0){ openDetail=function(id){ previousOpenDetailP0(id); const x=(window.all||all||[]).find(i=>String(i.id)===String(id)); if(!x)return; const trust=document.querySelector('#mTrust'); if(trust){trust.insertAdjacentHTML('beforeend', detailEvidenceP0(x));} const head=document.querySelector('#mHead'); if(head){head.textContent=(x.source||'source')+' · '+(x.source_id||x.id)+' · détail vérifiable';} }; }
setTimeout(()=>{try{ if(typeof all!=='undefined') window.all=all; addCardStatusP0(); }catch(e){console.error('p0 polish failed',e)}},500);
window.__p0ProductPolish={statusHtmlP0,detailEvidenceP0};
})();
</script>
'''

CHANGES_PATCH_JS = r'''
<script id="changesP0Patch">
(function(){
const toolbar=document.querySelector('.toolbar');
if(toolbar && !document.querySelector('.changesHelp')) toolbar.insertAdjacentHTML('afterend','<p class="changesHelp">Astuce : les baisses/hausses/réapparitions sont mélangées avec les disparitions dans le journal. Utilise les boutons ci-dessus : ils masquent vraiment les autres cartes et le compteur indique les éléments visibles.</p>');
function normP0(s){return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');}
const q=document.getElementById('q'); const buttons=[...document.querySelectorAll('[data-filter]')]; const cards=[...document.querySelectorAll('.change-card')]; const count=document.getElementById('countLabel'); let filter=(document.querySelector('[data-filter].active')||{}).dataset?.filter||'all';
function matches(card){return filter==='all'||(filter==='price_down'&&card.dataset.type==='price_changed'&&card.dataset.direction==='down')||(filter==='price_up'&&card.dataset.type==='price_changed'&&card.dataset.direction==='up')||card.dataset.type===filter;}
function applyP0(){const query=normP0(q&&q.value); let n=0; cards.forEach(card=>{const show=matches(card)&&(!query||normP0(card.dataset.q).includes(query)); card.hidden=!show; card.style.display=show?'':'none'; if(show)n++;}); if(count) count.textContent=n+' élément(s) visible(s) — filtre '+filter+'.';}
buttons.forEach(b=>{b.addEventListener('click',e=>{e.preventDefault(); filter=b.dataset.filter; buttons.forEach(x=>x.classList.toggle('active',x===b)); applyP0();}, true);}); if(q) q.addEventListener('input',applyP0, true); applyP0(); window.__changesP0Apply=applyP0;
})();
</script>
'''


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def hours_since(value: Any) -> float | None:
    dt = parse_dt(value)
    if not dt:
        return None
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 2)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def strip_block(text: str, marker_id: str) -> str:
    return re.sub(rf'\n?<(script|style) id="{re.escape(marker_id)}".*?</\1>\s*', '', text, flags=re.S)


def enrich_json(app: Path) -> dict[str, Any]:
    listings_path = app / 'listings.json'
    data = read_json(listings_path, {'listings': []})
    items = data.get('listings') or []
    changes = (read_json(app / 'changes.json', {'changes': []}).get('changes') or [])
    source_health = read_json(app / 'source_health.json', {'sources': []}).get('sources') or []
    dedup_groups = read_json(app / 'dedup_groups.json', {'groups': []}).get('groups') or []

    history_by_id: dict[str, list[dict[str, Any]]] = {}
    for ev in changes:
        lid = str(ev.get('listing_id') or '')
        if lid:
            history_by_id.setdefault(lid, []).append({k: ev.get(k) for k in ['event_id','event_type','event_at','old_value','new_value','delta_eur','direction','severity']})
    health_by_source = {str(s.get('source') or '').lower(): s for s in source_health}
    dedup_by_id: dict[str, dict[str, Any]] = {}
    for g in dedup_groups:
        member_ids = [str(x) for x in (g.get('member_ids') or [])]
        for mid in member_ids:
            dedup_by_id[mid] = {
                'group_id': g.get('group_id'),
                'confidence': g.get('confidence'),
                'decision': g.get('decision'),
                'count': max(0, len(member_ids) - 1),
                'member_ids': member_ids,
                'links': g.get('links') or [],
                'explanations': g.get('explanations') or [],
            }

    now = datetime.now(timezone.utc)
    status_counts: dict[str, int] = {}
    for x in items:
        last = x.get('seen_last_at') or x.get('published_at')
        age = hours_since(last)
        if age is None:
            status = 'unknown'; label = 'Active · date n.c.'
        elif age <= 36:
            status = 'fresh'; label = 'Active récente'
        elif age <= 96:
            status = 'attention'; label = 'Active à revérifier'
        else:
            status = 'stale'; label = 'Active ancienne'
        hist = history_by_id.get(str(x.get('id') or ''), [])
        x['_p0'] = {
            'availability': {'status': status, 'label': label, 'last_seen_at': last, 'age_hours': age},
            'history': {
                'events': hist[:10],
                'price_changes': sum(1 for e in hist if e.get('event_type') == 'price_changed'),
                'disappeared': sum(1 for e in hist if e.get('event_type') == 'disappeared'),
                'reappeared': sum(1 for e in hist if e.get('event_type') == 'reappeared'),
            },
            'duplicates': dedup_by_id.get(str(x.get('id') or ''), {'count': 0, 'links': []}),
            'source_health': {k: health_by_source.get(str(x.get('source') or '').lower(), {}).get(k) for k in ['status','severity','reason','age_hours','active_rows','active_with_image']},
            'generated_at': now.isoformat(),
        }
        status_counts[status] = status_counts.get(status, 0) + 1

    data['p0_product_polish'] = {
        'version': 'p0_product_polish_v1',
        'generated_at': now.isoformat(),
        'availability_counts': status_counts,
        'changes_joined': sum(1 for x in items if (x.get('_p0') or {}).get('history', {}).get('events')),
        'dedup_joined': sum(1 for x in items if (x.get('_p0') or {}).get('duplicates', {}).get('count')),
    }
    listings_path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    (app / 'p0_product_polish.json').write_text(json.dumps(data['p0_product_polish'], ensure_ascii=False, indent=2), encoding='utf-8')
    return data['p0_product_polish']


def enhance_index(app: Path) -> None:
    p = app / 'index.html'
    text = p.read_text(encoding='utf-8')
    text = strip_block(text, 'p0ProductPolish')
    text = text.replace(P0_CSS, '')
    if '</style>' in text and '.statusStrip{' not in text:
        text = text.replace('</style>', P0_CSS + '\n</style>', 1)
    text = text.replace('</body></html>', P0_JS + '\n</body></html>')
    p.write_text(text, encoding='utf-8')


def enhance_changes(app: Path) -> None:
    p = app / 'changes.html'
    if not p.exists():
        return
    text = p.read_text(encoding='utf-8')
    text = strip_block(text, 'changesP0Patch')
    if '</style>' in text and '.changesHelp{' not in text:
        text = text.replace('</style>', P0_CSS + '\n</style>', 1)
    text = text.replace('</body></html>', CHANGES_PATCH_JS + '\n</body></html>') if '</body></html>' in text else text + CHANGES_PATCH_JS
    p.write_text(text, encoding='utf-8')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default='artifacts/app')
    args = ap.parse_args()
    app = Path(args.app).resolve()
    if not (app / 'listings.json').exists():
        raise SystemExit(f'missing listings file: {app / "listings.json"}')
    stats = enrich_json(app)
    skipped = []
    if (app / 'index.html').exists():
        enhance_index(app)
    else:
        skipped.append('index.html')
    if (app / 'changes.html').exists():
        enhance_changes(app)
    else:
        skipped.append('changes.html')
    print(json.dumps({'ok': True, 'app': str(app), 'stats': stats, 'skipped_missing_files': skipped}, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
