#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

ROOT = Path('/opt/data/projects/reunion-immo-search')
APP = ROOT / 'artifacts' / 'app'
INDEX = APP / 'index.html'
TS = dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
BAK = APP.parent / f'app.bak.wave3-lot-b-home-cards-detail-{TS}'

CSS = r'''
/* Wave 3 Lot B: homepage new cards + useful analysis + local precision */
.card.wave3New{background:#fff9db;border-color:#f4d38d;box-shadow:0 16px 45px rgba(120,82,0,.13)}
.card.wave3New .body{background:linear-gradient(180deg,rgba(255,249,219,.78),rgba(255,253,248,.95))}.card.wave3New .photo{outline:1px solid rgba(244,211,141,.55)}
.wave3FreshBadge{display:inline-flex;align-items:center;gap:5px;margin:7px 0 2px;border:1px solid #f4d38d;background:#fff3bf;color:#7c4a03;border-radius:999px;padding:5px 8px;font-size:11.5px;font-weight:900}.wave3FreshBadge small{font-weight:750;color:#8a5a00}.wave3LocLine{margin-top:5px;color:#475569;font-size:12px;line-height:1.35}.wave3LocLine strong{color:#134e4a}.wave3LocPill{background:#f2f7f6;color:#245b55;border-color:#cfe7df}.wave3ScorePrefix{font-weight:950;color:#134e4a}.wave3AnalysisBox{border:1px solid #d7e5ff;background:#f8fbff;border-radius:14px;padding:12px;margin:0 0 11px;color:#274060}.wave3AnalysisBox h4{margin:0 0 7px;font-size:14px;color:#134e4a}.wave3AnalysisGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.wave3AnalysisBox p,.wave3AnalysisBox li{font-size:12.5px;line-height:1.45}.wave3AnalysisBox p{margin:0 0 7px}.wave3AnalysisBox ul{margin:0;padding-left:18px}.wave3AnalysisScore{font-size:18px;font-weight:950;color:#134e4a}.wave3AnalysisWarn{color:#7c4a03}.wave3DescMark{background:#fff3bf;border-radius:5px;padding:0 3px;box-shadow:inset 0 -1px 0 #f4d38d}.desc .wave3DescMark{font-weight:650}.wave3PrecisionNote{font-size:12px;color:var(--muted);margin-top:4px}
@media(max-width:580px){.wave3AnalysisGrid{grid-template-columns:1fr}.wave3FreshBadge,.wave3LocLine{font-size:11.5px}.wave3AnalysisBox{padding:10px}.wave3AnalysisScore{font-size:16px}}
'''

SCRIPT = r'''
<script id="wave3LotBHomeCardsDetail">
(function(){
function escB(s){return (s??'').toString().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function normB(s){return String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim().replace(/\s+/g,' ');}
function uniqB(a){return [...new Set((a||[]).filter(Boolean).map(x=>String(x).replace(/\s+/g,' ').trim()).filter(Boolean))];}
function capB(s){s=String(s||'').trim(); return s?s.charAt(0).toUpperCase()+s.slice(1):s;}
function parsePubB(v){
  const s=String(v||'').trim(); if(!s)return null;
  let m=s.match(/il y a\s*(\d+)\s*(min|mn|h|j|jour|jours|sem)/i); if(m){const n=Number(m[1]); const u=m[2].toLowerCase(); if(u.startsWith('min')||u==='mn')return {ageHours:n/60,label:s,relative:true}; if(u==='h')return {ageHours:n,label:s,relative:true}; if(u.startsWith('j'))return {ageHours:n*24,label:s,relative:true}; if(u.startsWith('sem'))return {ageHours:n*24*7,label:s,relative:true};}
  let d=null; const dm=s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/); if(dm){let y=Number(dm[3]); if(y<100)y+=2000; d=new Date(Date.UTC(y,Number(dm[2])-1,Number(dm[1])));} else {const t=Date.parse(s); if(!Number.isNaN(t))d=new Date(t);}
  if(d){const h=(Date.now()-d.getTime())/36e5; return {ageHours:h,label:s,relative:false};}
  return null;
}
function recentInfoB(x){const p=parsePubB(x.published_at); if(p&&p.ageHours>=-12&&p.ageHours<=72)return p; return null;}
function fmtSeenB(s){if(!s)return ''; try{return new Date(s).toLocaleDateString('fr-FR',{day:'2-digit',month:'2-digit'})+' '+new Date(s).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});}catch(e){return String(s||'');}}
function precisionTermsB(x){
  const li=x.location_intelligence||{}, out=[];
  [li.district_best, li.precise_location_label, x.district].forEach(v=>{if(v && normB(v)!==normB(x.city))out.push(String(v));});
  const desc=String(x.description||'');
  const loc=String(x.location||''); if(loc && normB(loc)!==normB(x.city))out.push(loc);
  const regs=[/\b(?:rue|avenue|av\.?|chemin|impasse|allée|allee|route|bd|boulevard)\s+([A-ZÀ-ÖØ-Ýa-zà-öø-ÿ0-9'’ -]{3,45})/g, /\b(?:quartier|secteur)\s+([A-ZÀ-ÖØ-Ýa-zà-öø-ÿ0-9'’ -]{3,38})/g, /\b(?:proche|près de|pres de|à proximité de|a proximite de|proximité immédiate des?|proximite immediate des?)\s+([A-ZÀ-ÖØ-Ýa-zà-öø-ÿ0-9'’ -]{3,42})/g];
  for(const rg of regs){let m; while((m=rg.exec(desc))&&out.length<8){const full=m[0].replace(/[.,;:]+$/,'').trim(); if(full.length>=5)out.push(capB(full));}}
  return uniqB(out).filter(t=>normB(t)!==normB(x.city)).slice(0,4);
}
function primaryLocationB(x){
  const li=x.location_intelligence||{};
  const label=li.precise_location_label || (li.district_best && (li.commune_inferred||x.city) ? `${li.district_best} · ${li.commune_inferred||x.city}` : '');
  if(label && normB(label)!==normB(x.city)) return String(label).replace(/\s*·\s*/g,' · ');
  if(x.location && normB(x.location)!==normB(x.city)) return String(x.location).replace(/\s*·\s*/g,' · ');
  return String(x.city||'Localisation non précisée');
}
function shortPrecisionB(x){const ts=precisionTermsB(x); if(!ts.length)return ''; let best=ts.find(t=>/\b(rue|avenue|chemin|impasse|route|boulevard)\b/i.test(t))||ts.find(t=>/\b(proche|proxim|quartier|secteur)\b/i.test(t))||ts[0]; return best.replace(/\s*·\s*/g,' · ');}
function opportunityScoreB(x){return Number((x.opportunity_analysis||{}).score ?? x.opportunity_score ?? x.score ?? 0)||0;}
function analysisSummaryB(x){const a=x.opportunity_analysis||{}, score=opportunityScoreB(x), price=Number(x.price||0), surface=Number(x.surface||0), rooms=x.rooms||''; const loc=shortPrecisionB(x)||x.location||x.city||'Localisation source';
  const plus=[]; const warn=[]; if(score>=80)plus.push('score opportunité élevé'); else if(score>=60)plus.push('profil à étudier'); else warn.push('score modéré: comparer avec le marché');
  if(price&&surface)plus.push(Math.round(price/surface)+' €/m²/mois'); if((x.local_image_urls||[]).length>1)plus.push((x.local_image_urls||[]).length+' photos'); else warn.push('photos limitées ou à vérifier');
  if(rooms)plus.push('T/F'+rooms); if(x.furnished)plus.push(x.furnished); const li=x.location_intelligence||{}; if(li.district_best||/\b(rue|proche|proximité|proximite)\b/i.test(loc))plus.push('localisation exploitable: '+loc); else warn.push('quartier/rue non explicite dans la source');
  (a.warnings||[]).slice(0,2).forEach(w=>warn.push(w)); (a.reasons||[]).slice(0,2).forEach(r=>plus.push(r));
  return {score,label:a.label||'Analyse opportunité',confidence:a.confidence||'à vérifier',plus:uniqB(plus).slice(0,5),warn:uniqB(warn).slice(0,4),loc};
}
function scoreButtonTextB(x){const s=opportunityScoreB(x); return (s?`${s}/100 `:'')+'Analyse';}
function addCardEnhancementsB(){
  document.querySelectorAll('.card').forEach(card=>{
    const x=(window.all||all||[]).find(i=>String(i.id)===String(card.dataset.id)); if(!x)return; const body=card.querySelector('.body'), actions=card.querySelector('.actions'); if(!body||!actions)return;
    const btn=card.querySelector('button[data-open]'); if(btn){const s=opportunityScoreB(x); btn.innerHTML=s?`<span class="wave3ScorePrefix">${escB(s)}/100</span> Analyse`:'Analyse'; btn.setAttribute('aria-label', scoreButtonTextB(x)+' détaillée');}
    const ri=recentInfoB(x); card.classList.toggle('wave3New', !!ri); if(ri&&!body.querySelector('.wave3FreshBadge')){const seen=fmtSeenB(x.seen_last_at); const html=`<div class="wave3FreshBadge" title="Publication source: ${escB(x.published_at||'n.c.')}">Nouveau · ${escB(ri.label||'≤ 3 jours')}${seen?` <small>vu ${escB(seen)}</small>`:''}</div>`; const title=body.querySelector('.title'); (title||body.firstElementChild).insertAdjacentHTML(title?'afterend':'afterend',html);}
    const primary=primaryLocationB(x), sp=shortPrecisionB(x); if(primary&&!body.querySelector('.wave3LocLine')){const loc=body.querySelector('.loc'); if(loc){loc.textContent=primary;} const note=(sp && normB(sp)!==normB(primary)) ? ` <span>Note: ${escB(sp)}</span>` : ''; const html=`<div class="wave3LocLine">Précision source: <strong>${escB(primary)}</strong>${note}</div>`; body.insertBefore(Object.assign(document.createElement('div'),{innerHTML:html}).firstElementChild, actions);}
    const meta=body.querySelector('.meta'); if(meta&&sp&&!meta.querySelector('.wave3LocPill')) meta.insertAdjacentHTML('beforeend',`<span class="pill wave3LocPill">${escB(sp).slice(0,64)}</span>`);
  });
}
function highlightDescB(x){const desc=document.querySelector('#mDesc'); if(!desc)return; let text=String(desc.textContent||x.description||'').replace(/\s+/g,' ').trim(); if(!text){desc.textContent='Description source non disponible.';return;} const terms=precisionTermsB(x).flatMap(t=>[t, t.replace(/^(rue|avenue|chemin|impasse|route|boulevard|quartier|secteur|proche|près de|pres de|à proximité de|a proximite de)\s+/i,'')]).map(t=>t.trim()).filter(t=>t.length>=4).slice(0,8); if(!terms.length){desc.textContent=text;return;} let html=escB(text); for(const term of uniqB(terms).sort((a,b)=>b.length-a.length)){const safe=escB(term).replace(/[.*+?^${}()|[\]\\]/g,'\\$&'); html=html.replace(new RegExp('('+safe+')','gi'),'<mark class="wave3DescMark">$1</mark>');} desc.innerHTML=html;
}
function analysisHtmlB(x){const a=analysisSummaryB(x); const plus=a.plus.length?'<ul>'+a.plus.map(v=>`<li>${escB(v)}</li>`).join('')+'</ul>':'<p>Peu de signaux positifs extraits automatiquement.</p>'; const warn=a.warn.length?'<ul>'+a.warn.map(v=>`<li>${escB(v)}</li>`).join('')+'</ul>':'<p>Aucun signal bloquant évident; confirmer prix, disponibilité et adresse exacte.</p>'; return `<div class="wave3AnalysisBox" data-wave3-analysis><h4>Analyse utile</h4><div class="wave3AnalysisGrid"><div><div class="wave3AnalysisScore">${escB(a.score)}/100 · ${escB(a.label)}</div><p>Confiance: ${escB(a.confidence)} · zone: ${escB(a.loc)}</p><strong>Points favorables</strong>${plus}</div><div><strong class="wave3AnalysisWarn">À vérifier</strong>${warn}<p class="wave3PrecisionNote">Analyse construite depuis prix, surface, score, photos, historique visible et indices source — pas une simple reprise de description.</p></div></div></div>`;}
const prevRenderB=typeof render==='function'?render:null; if(prevRenderB){render=function(res){prevRenderB(res); addCardEnhancementsB();};}
const prevOpenB=typeof openDetail==='function'?openDetail:null; if(prevOpenB){openDetail=function(id){prevOpenB(id); const x=(window.all||all||[]).find(i=>String(i.id)===String(id)); if(!x)return; const price=document.querySelector('#mPrice'); if(price){const s=opportunityScoreB(x); price.textContent=(s?`${s}/100 · `:'')+price.textContent;} const loc=document.querySelector('#mLoc'); const primary=primaryLocationB(x), sp=shortPrecisionB(x); if(loc&&primary)loc.textContent=primary+(sp&&normB(sp)!==normB(primary)?' · note: '+sp:''); highlightDescB(x); const trust=document.querySelector('#mTrust'); if(trust){trust.querySelector('[data-wave3-analysis]')?.remove(); trust.insertAdjacentHTML('afterbegin',analysisHtmlB(x));} };}
setTimeout(()=>{try{if(typeof all!=='undefined')window.all=all; addCardEnhancementsB();}catch(e){console.error('wave3 lot B failed',e)}},1200);
window.__wave3LotBHomeCardsDetail={recentInfoB,precisionTermsB,shortPrecisionB,analysisSummaryB,addCardEnhancementsB,highlightDescB};
})();
</script>
'''


def main() -> int:
    if not INDEX.exists():
        raise SystemExit(f'missing {INDEX}')
    if not BAK.exists():
        shutil.copytree(APP, BAK)
    html = INDEX.read_text(encoding='utf-8')
    html = re.sub(r'\n<script id="wave3LotBHomeCardsDetail">.*?</script>\n?', '\n', html, flags=re.S)
    html = re.sub(r'\n?/\* Wave 3 Lot B: homepage new cards \+ useful analysis \+ local precision \*/.*?\.wave3PrecisionNote\{[^}]+\}\n@media\(max-width:580px\)\{\.wave3AnalysisGrid\{grid-template-columns:1fr\}\.wave3FreshBadge,\.wave3LocLine\{font-size:11\.5px\}\.wave3AnalysisBox\{padding:10px\}\.wave3AnalysisScore\{font-size:16px\}\}\n?', '\n', html, flags=re.S)
    if '/* Wave 3 Lot B: homepage new cards + useful analysis + local precision */' not in html:
        html = html.replace('</style>', CSS + '\n</style>', 1)
    html = html.replace('</body></html>', SCRIPT + '\n</body></html>')
    INDEX.write_text(html, encoding='utf-8')
    print({'ok': True, 'index': str(INDEX), 'backup': str(BAK), 'script': 'wave3LotBHomeCardsDetail'})
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
