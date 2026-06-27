#!/usr/bin/env python3
from pathlib import Path
import re, shutil, datetime
APP=Path('/opt/data/projects/reunion-immo-search/artifacts/app')
INDEX=APP/'index.html'
TS=datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
BAK=APP.parent/f'app.bak.mobile-detail-cleanup-v4-{TS}'
SCRIPT=r'''

<script id="mobileDetailCleanupV4">
(function(){
function esc4(s){return (s??'').toString().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function cleanDesc4(s){
  s=String(s||'').replace(/\s+/g,' ').trim();
  s=s.replace(/Annonce\s+\w+\s+collectée\s+par\s+CDP\.?/ig,'').trim();
  s=s.replace(/\b(commune|quartier|source)?\s*serp_view=[^\s]+/ig,'').trim();
  s=s.replace(/\bsearch=[^\s]+/ig,'').trim();
  return s;
}
function validHousing4(x){
  const d=x?.housing_details||{}, bd=d.bedroom_distribution||{};
  const bath=Number(d.bathrooms?.count||0), wc=Number(d.wc?.count||0);
  const layout=(d.layout?.label||'Non précisé');
  const totalBeds=Number(x?.bedrooms||0);
  const low=Number(bd.ground_floor_count||0), up=Number(bd.upstairs_count||0);
  const bedroomDistOk=(low||up) && (!totalBeds || (low+up)<=totalBeds);
  return {d,bd,bath,wc,layout,totalBeds,low,up,bedroomDistOk,has: bath||wc||(layout&&layout!=='Non précisé')||bedroomDistOk};
}
function compactHousingPills4(x){
  const h=validHousing4(x), out=[];
  if(h.bath) out.push(`${h.bath} SDB`);
  if(h.wc) out.push(`${h.wc} WC`);
  if(h.layout==='Duplex / étage(s)') out.push('Duplex');
  else if(h.layout==='Tout RDC / plain-pied') out.push('Plain-pied');
  else if(h.layout==='RDC mentionné') out.push('RDC');
  else if(h.layout==='Étage mentionné') out.push('Étage');
  if(h.bedroomDistOk){
    const parts=[]; if(h.low) parts.push(`${h.low} ch. RDC`); if(h.up) parts.push(`${h.up} ch. étage`);
    out.push(parts.join(' · '));
  }
  return out.slice(0,3).map(t=>`<span class="pill housingPill cleanHousingPill">${esc4(t)}</span>`).join('');
}
function stripOldHousing4(html){
  return html.replace(/<span class="pill housingPill">.*?<\/span>/g,'').replace(/<span class="pill housingPill cleanHousingPill">.*?<\/span>/g,'');
}
if(typeof card==='function'){
  const prevCard4=card;
  card=function(x){
    let html=stripOldHousing4(prevCard4(x));
    const pills=compactHousingPills4(x);
    return pills?html.replace('<div class="actions">',pills+'<div class="actions">'):html;
  };
}
if(typeof openDetail==='function'){
  const prevOpen4=openDetail;
  openDetail=function(id){
    prevOpen4(id);
    const x=(window.all||all||[]).find(i=>String(i.id)===String(id)); if(!x)return;
    // Clean technical source/query fragments from visible description.
    const desc=document.querySelector('#mDesc'); if(desc){const c=cleanDesc4(x.description); desc.textContent=c||'Description source non disponible.';}
    // Remove previous noisy housing boxes inserted by v3/P0, then insert only if useful.
    document.querySelectorAll('.housingDetailBox').forEach(el=>el.remove());
    const trust=document.querySelector('#mTrust'); if(!trust)return;
    const h=validHousing4(x); if(!h.has)return;
    const ev=[...(h.d.bathrooms?.evidence||[]),...(h.d.wc?.evidence||[]),...(h.d.layout?.evidence||[]),...(h.bd.evidence||[])].map(cleanDesc4).filter(Boolean).slice(0,3);
    const bedLine=h.bedroomDistOk?`${h.low?esc4(h.low)+' ch. RDC':''}${h.low&&h.up?' · ':''}${h.up?esc4(h.up)+' ch. étage':''}`:'non précisé';
    const html=`<div class="evidenceBox housingDetailBox cleanHousingBox"><h4>Pièces d’eau / niveaux</h4><ul>${h.bath?`<li>Sdb/eau: ${h.bath}</li>`:''}${h.wc?`<li>WC: ${h.wc}</li>`:''}${h.layout&&h.layout!=='Non précisé'?`<li>Niveaux: ${esc4(h.layout)}</li>`:''}${h.bedroomDistOk?`<li>Chambres: ${bedLine}</li>`:''}</ul>${ev.length?`<p>Indices source: ${esc4(ev.join(' · '))}</p>`:''}</div>`;
    trust.insertAdjacentHTML('afterbegin',html);
    // If the main image fails/blank, collapse it to a small explicit message instead of a huge beige empty block.
    const img=document.querySelector('#mImg img');
    if(img){img.addEventListener('error',()=>{document.querySelector('#mImg').innerHTML='<div class="no-photo compactNoPhoto">Photo source indisponible</div>';},{once:true}); if(img.complete && img.naturalWidth===0){document.querySelector('#mImg').innerHTML='<div class="no-photo compactNoPhoto">Photo source indisponible</div>';}}
  };
}
// Remove low-value duplicate match/geo badges from cards; search chips already explain the match.
function cleanupCardNoise4(){document.querySelectorAll('.card .matchReasons,.card .geoHint').forEach(el=>el.remove());}
const prevRender4=typeof render==='function'?render:null; if(prevRender4){render=function(res){prevRender4(res); cleanupCardNoise4();};}
setTimeout(()=>{try{if(typeof apply==='function')apply(false); cleanupCardNoise4();}catch(e){console.error('mobile cleanup v4 failed',e)}},900);
window.__mobileDetailCleanupV4={compactHousingPills4,cleanDesc4,validHousing4};
})();
</script>
'''
CSS='''.cleanHousingPill{background:#eef4ff;color:#274060;font-weight:760}.compactNoPhoto{min-height:96px;display:flex;align-items:center;justify-content:center;color:var(--muted)}@media(max-width:580px){.cleanHousingBox ul{margin-bottom:4px}.cleanHousingBox p{font-size:11.5px}.card .matchReasons,.card .geoHint{display:none!important}}\n'''
if not BAK.exists(): shutil.copytree(APP,BAK)
html=INDEX.read_text()
html=re.sub(r'\n<script id="mobileDetailCleanupV4">.*?</script>\n','\n',html,flags=re.S)
if '.cleanHousingPill{' not in html:
    html=html.replace('</style>',CSS+'</style>')
html=html.replace('</body></html>',SCRIPT+'\n</body></html>')
INDEX.write_text(html)
print({'ok':True,'backup':str(BAK),'index':str(INDEX)})
