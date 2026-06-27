#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any

COMMUNE_ALIASES: dict[str, list[str]] = {
    'Saint-Denis': ['saint denis', 'st denis', 'st-denis', 'denis', 'sainte clotilde', 'ste clotilde', 'moufia', 'bellepierre', 'chaudron', 'camelia', 'camélias', 'montgaillard', 'vauban', 'barachois'],
    'Sainte-Marie': ['sainte marie', 'ste marie', 'ste-marie', 'la convenance'],
    'Sainte-Suzanne': ['sainte suzanne', 'ste suzanne', 'quartier francais', 'quartier français', 'bagatelle', 'deux rives', 'bocage'],
    'Saint-André': ['saint andre', 'saint andré', 'st andre', 'st-andre', 'cambuston', 'champ borne', 'ravine creuse'],
    'Saint-Benoît': ['saint benoit', 'saint benoît', 'st benoit', 'bras fusil', 'beaulieu', 'sainte anne'],
    'Saint-Paul': ['saint paul', 'st paul', 'st-paul', 'saint gilles', 'st gilles', 'la saline', 'hermitage', 'ermitage', 'boucan canot', 'plateau caillou', 'guillaume'],
    'Saint-Pierre': ['saint pierre', 'st pierre', 'st-pierre', 'terre sainte', 'ravine des cabris', 'bois d olives', 'bois dolives', 'ligne paradis'],
    'Le Tampon': ['le tampon', 'tampon', '14eme', '14ème', 'pont d yves', 'trois mares', 'terrain fleury', 'bras de pontho'],
    'Saint-Leu': ['saint leu', 'st leu', 'st-leu', 'piton saint leu', 'la fontaine', 'etang saint leu', 'étang saint leu'],
    'La Possession': ['la possession', 'possession', 'riviere des galets', 'rivière des galets', 'pichette', 'dos d ane', "dos d'ane"],
}

SPECIFIC_LOCATION_ALIASES: dict[str, dict[str, Any]] = {
    'Rivière des Pluies': {'aliases': ['rivière des pluies', 'riviere des pluies', 'rivières des pluies', 'rivieres des pluies'], 'commune': 'Sainte-Marie'},
    'Beauséjour': {'aliases': ['beauséjour', 'beausejour'], 'commune': 'Sainte-Marie'},
    'Grande Montée': {'aliases': ['grande montée', 'grande montee', 'la grande montée', 'la grande montee'], 'commune': 'Sainte-Marie'},
    'Duparc': {'aliases': ['duparc'], 'commune': 'Sainte-Marie'},
    'Bretagne': {'aliases': ['bretagne', 'la bretagne'], 'commune': 'Saint-Denis'},
}

AMENITY_ALIASES: dict[str, list[str]] = {
    'Meublé': ['meuble', 'meublé', 'meublee', 'meublée', 'furnished'],
    'Parking': ['parking', 'stationnement', 'garage', 'box', 'place de parking'],
    'Varangue / terrasse': ['varangue', 'terrasse', 'balcon', 'veranda', 'véranda'],
    'Jardin': ['jardin', 'terrain privatif'],
    'Piscine': ['piscine'],
    'Climatisation': ['clim', 'climatisation', 'climatise', 'climatisé'],
    'Ascenseur': ['ascenseur'],
    'Animaux': ['animaux acceptes', 'animaux acceptés', 'animaux autorises', 'animaux autorisés'],
}

EXAMPLES = [
    'T2 Saint-Denis moins 900 meublé parking',
    'studio Moufia proche université',
    'maison Saint-Paul jardin piscine',
    'Sainte-Marie 2 chambres varangue',
    'Saint-Pierre moins de 1200 terrasse',
]

CSS = r'''
.searchWrap{flex:1;min-width:280px}.searchFeedback{width:100%;padding:8px 4px 0}.nlChips{display:flex;gap:7px;flex-wrap:wrap;min-height:0}.nlchip{display:inline-flex;align-items:center;gap:6px;border:1px solid #cfe7df;background:#effaf6;color:#134e4a;border-radius:999px;padding:6px 10px;font-size:12px;font-weight:820;box-shadow:0 2px 8px rgba(15,118,110,.06)}.nlchip.soft{background:#fff8e6;border-color:#f4d38d;color:#7c4a03}.nlchip button{border:0;background:transparent;color:inherit;font-weight:900;cursor:pointer;padding:0 1px}.understood{margin-top:6px;color:var(--muted);font-size:13px}.suggestions{display:flex;gap:7px;overflow:auto;padding-top:7px}.suggestions button{white-space:nowrap;border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 9px;color:var(--muted);font-weight:750;cursor:pointer}.suggestions button:hover{color:var(--brand);border-color:#b9ddd5}.matchReasons{display:flex;gap:5px;flex-wrap:wrap;margin-top:8px}.reason{font-size:11px;font-weight:760;background:#f2f7f6;color:#245b55;border-radius:999px;padding:4px 7px}.geoHint{color:var(--muted);font-size:12px;margin-top:7px}.priceOp{display:flex;gap:6px;flex-wrap:wrap;margin-top:7px}.priceOp span{font-size:12px;color:var(--muted);font-weight:760}.priceOp button{border:1px solid #f4d38d;background:#fff8e6;color:#7c4a03;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:850;cursor:pointer}.emptyActions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.emptyActions button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:9px 12px;font-weight:800;cursor:pointer}.card .photo img{background:#eee;object-fit:cover}.card .photo img.is-broken{display:none}@media(max-width:580px){.searchWrap{min-width:100%;order:3}.searchFeedback{padding-top:4px}.nlChips{flex-wrap:nowrap;overflow:auto;padding-bottom:2px}.nlchip{white-space:nowrap}.suggestions{display:none}.understood{font-size:12px}}
'''

JS_TEMPLATE = r'''
<script id="naturalSearchV2">
(function(){
const SEARCH_ONTOLOGY = __ONTOLOGY__;
function ready(fn){ if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn); else fn(); }
function norm2(s){return String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim().replace(/\s+/g,' ');}
function esc2(s){return (s??'').toString().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function itemHay(x){return norm2([x.id,x.source_id,x.title,x.city,x.district,x.location,x.region,x.type,x.source,x.agency,x.url,x.description,x.rooms?('t'+x.rooms+' f'+x.rooms+' '+x.rooms+' pieces '+x.rooms+' p'):'',x.furnished||'',(x.feature_tags||[]).join(' '),x.location_intelligence?.district_best||'',x.location_intelligence?.precise_location_label||'',x.location_intelligence?.commune_inferred||'',(x.location_intelligence?.district_hints||[]).join(' '),(x.opportunity_analysis?.label||''),(x.opportunity_analysis?.reasons||[]).join(' '),(x.opportunity_analysis?.warnings||[]).join(' ')].join(' '));}
function locationAliases(){const out=[]; Object.entries(SEARCH_ONTOLOGY.specific_locations||{}).forEach(([label,info])=>{const aliases=[label,...(info.aliases||[])].map(norm2).filter(Boolean); out.push({label,commune:info.commune||'',aliases,strict:true,specific:true});}); Object.entries(SEARCH_ONTOLOGY.commune_aliases||{}).forEach(([label,aliases])=>{out.push({label,commune:label,aliases:[label,...(aliases||[])].map(norm2).filter(Boolean),strict:true,specific:false});}); return out.sort((a,b)=>(b.specific?1:0)-(a.specific?1:0) || Math.max(...b.aliases.map(x=>x.length))-Math.max(...a.aliases.map(x=>x.length)));}
function amenityAliases(){return Object.entries(SEARCH_ONTOLOGY.amenity_aliases||{}).flatMap(([label,aliases])=>(aliases||[]).map(a=>[norm2(a),label])).sort((a,b)=>b[0].length-a[0].length);}
function addChip(chips,type,label,value,strict=true){if(!label||chips.some(c=>c.type===type && c.label===label))return; chips.push({type,label,value:value??label,strict});}
function parseNatural(q){let raw=norm2(q).replace(/\bst\b/g,'saint').replace(/\bste\b/g,'sainte'); let work=' '+raw+' '; let tokens=[], rooms=[], maxPrice=null, minPrice=null, minSurface=null, typeHint='', chips=[], amenities=[], excludeAmenities=[], near=[], locations=[], barePrice=null, priceOp=null;
 [...work.matchAll(/\b[tf]\s*([1-9])\b/g)].forEach(m=>{const n=Number(m[1]); rooms.push(n); addChip(chips,'rooms','T/F'+n,n); work=work.replace(m[0],' ');});
 if(/\bstudio\b/.test(work)){rooms.push(1); addChip(chips,'rooms','Studio / T1',1); work=work.replace(/\bstudio\b/g,' ');}
 const between=work.match(/(?:entre|de)\s*(\d{3,5})\s*(?:et|a|à|-)\s*(\d{3,5})/); if(between){minPrice=Number(between[1]); maxPrice=Number(between[2]); priceOp='range'; addChip(chips,'budget','Entre '+minPrice+' et '+maxPrice+' €',{min:minPrice,max:maxPrice}); work=work.replace(between[0],' ');}
 const pmin=work.match(/(?:plus de|plus|au dessus de|min|minimum|>=|>)\s*(\d{2,5})/); if(pmin){minPrice=Number(pmin[1]); priceOp='min'; addChip(chips,'budget','≥ '+minPrice+' €',minPrice); work=work.replace(pmin[0],' ');}
 const pmax=work.match(/(?:moins de|moins|sous|max|budget max|loyer max|jusqu a|jusqua|<=|<)\s*(\d{2,5})/); if(pmax){maxPrice=Number(pmax[1]); priceOp='max'; addChip(chips,'budget','≤ '+maxPrice+' €',maxPrice); work=work.replace(pmax[0],' ');}
 const peq=work.match(/(?:=|exactement|egal|égal)\s*(\d{3,5})|(\d{3,5})\s*(?:exactement|egal|égal)/); if(peq && !priceOp){barePrice=Number(peq[1]||peq[2]); priceOp='equal'; addChip(chips,'budget','= '+barePrice+' €',barePrice); work=work.replace(peq[0],' ');}
 const paround=work.match(/(?:autour de|environ|vers|aux alentours de)\s*(\d{3,5})|(\d{3,5})\s*(?:environ|autour)/); if(paround && !priceOp){barePrice=Number(paround[1]||paround[2]); priceOp='around'; addChip(chips,'budget','Autour de '+barePrice+' €',barePrice); work=work.replace(paround[0],' ');}
 if(!priceOp){const nums=[...work.matchAll(/\b(\d{3,5})\s*(?:€|eur|euros)?\b/g)].map(m=>Number(m[1])).filter(n=>n>=300&&n<=10000); if(nums.length){barePrice=Math.max(...nums); addChip(chips,'budget','Budget '+barePrice+' € : choisir ≤ ≥ = autour',barePrice,false);}}
 const m=work.match(/(\d{1,3})\s*(?:m2|m²|metres|metre|m)\b/); if(m){minSurface=Number(m[1]); addChip(chips,'surface','≥ '+minSurface+' m²',minSurface); work=work.replace(m[0],' ');}
 if(/\b(maison|villa)\b/.test(work)){typeHint='maison'; addChip(chips,'type','Maison / villa','maison');}
 if(/\b(appartement|appart|apt)\b/.test(work)){typeHint='appartement'; addChip(chips,'type','Appartement','appartement');}
 for(const loc of locationAliases()){ const hit=loc.aliases.find(a=>a.length>2 && work.includes(' '+a+' ')); if(hit){locations.push(loc); addChip(chips,'location',loc.label,loc.label); loc.aliases.forEach(a=>{if(a) work=work.replaceAll(' '+a+' ',' ');});}}
 [...raw.matchAll(/(?:proche|pres|près|vers|autour de|a proximite de|à proximité de)\s+([a-z0-9 ]{3,40})/g)].forEach(mm=>{const val=mm[1].trim().split(/\s+(?:moins|avec|sans|t\d|f\d|studio)\b/)[0].trim(); if(val){near.push(val); addChip(chips,'near','Proche '+val,val,false);}});
 const negativeFurnished=/\b(non|pas|sans)\s+meuble\b|\bnon meuble\b|\blocation nue\b|\bloue vide\b/.test(work); if(negativeFurnished){excludeAmenities.push('Meublé'); addChip(chips,'amenity_exclude','Non meublé','Meublé'); work=work.replace(/\b(non|pas|sans)\s+meuble\b|\bnon meuble\b|\blocation nue\b|\bloue vide\b/g,' ');}
 for(const [alias,label] of amenityAliases()){ if(alias && work.includes(' '+alias+' ')){ amenities.push(label); addChip(chips,'amenity',label,label,true); work=work.replaceAll(' '+alias+' ',' ');}}
 const stop=new Set(['le','la','les','l','un','une','des','du','de','d','a','au','aux','en','sur','dans','avec','sans','et','ou','pour','moins','sous','max','budget','loyer','jusqu','jusqua','entre','proche','pres','près','vers','autour','m2','m','metres','metre','min','minimum','plus','piece','pieces','p','ch','chambre','chambres','appartement','appart','apt','maison','villa','euro','euros','eur','meuble','non','pas']);
 tokens=work.split(/\s+/).filter(Boolean).filter(x=>!stop.has(x) && !/^[tf][1-9]$/.test(x) && !/^\d{1,5}$/.test(x)); return {raw,tokens:[...new Set(tokens)],rooms:[...new Set(rooms)],maxPrice,minPrice,barePrice,priceOp,minSurface,typeHint,amenities:[...new Set(amenities)],excludeAmenities:[...new Set(excludeAmenities)],near,locations,chips};}
function matchesLocation(x,loc){const hay=x._hay||itemHay(x); return loc.aliases.some(a=>a && hay.includes(a)) || (loc.commune && norm2(x.city)===norm2(loc.commune) && norm2(loc.label)===norm2(loc.commune));}
function isExplicitMeuble(x){const furnished=norm2(x.furnished||''); const tags=(x.feature_tags||[]).map(norm2); const analysis=x.description_analysis?.property_state?.furnished?.status; return furnished==='meuble'||furnished==='meublé'||tags.includes('meuble')||analysis==='meuble';}
function relScore(x,parsed){const hay=x._hay||''; let score=0; for(const t of (parsed.tokens||[])){if(hay.split(' ').includes(t)) score+=7; else if(hay.includes(t)) score+=3;} (parsed.amenities||[]).forEach(a=>{if((x.feature_tags||[]).includes(a)||hay.includes(norm2(a)))score+=12;}); (parsed.locations||[]).forEach(l=>{if(matchesLocation(x,l))score+=40;}); return score;}
function reasons(x,parsed){const out=[]; if(parsed.rooms?.length && parsed.rooms.includes(Number(x.rooms))) out.push('pièces OK'); if(parsed.maxPrice && x.price&&x.price<=parsed.maxPrice) out.push('budget OK'); if(parsed.minPrice && x.price&&x.price>=parsed.minPrice) out.push('budget OK'); (parsed.locations||[]).forEach(l=>{if(matchesLocation(x,l)) out.push(l.label);}); (parsed.amenities||[]).forEach(a=>{if((x.feature_tags||[]).includes(a)||(x._hay||'').includes(norm2(a))) out.push(a);}); (parsed.excludeAmenities||[]).forEach(a=>{if(a==='Meublé'&&!isExplicitMeuble(x)) out.push('non meublé demandé');}); if((x.local_image_urls||[]).length>1) out.push('galerie'); return out.slice(0,5);}
function priceOperatorHtml(parsed){if(!parsed.barePrice || parsed.priceOp) return ''; const v=parsed.barePrice; return `<div class="priceOp" id="priceOp"><span>${v} € : choisir</span><button data-price-op="max" data-price-value="${v}">≤</button><button data-price-op="min" data-price-value="${v}">≥</button><button data-price-op="equal" data-price-value="${v}">=</button><button data-price-op="around" data-price-value="${v}">autour</button></div>`;}
function renderChips(parsed){const root=document.querySelector('#nlChips'); if(!root)return; root.innerHTML=(parsed.chips||[]).map((c,i)=>`<span class="nlchip ${c.strict?'':'soft'}" title="${c.strict?'Filtre strict':'À préciser / préférence'}">${esc2(c.label)}<button type="button" data-chip-remove="${i}" aria-label="Retirer ${esc2(c.label)}">×</button></span>`).join('')+priceOperatorHtml(parsed); const txt=(parsed.chips||[]).map(c=>c.label).join(', '); const u=document.querySelector('#understood'); if(u) u.textContent=txt?('Compris : '+txt):'Tape une recherche naturelle : budget, ville, type, quartier, parking, meublé…'; const sug=document.querySelector('#suggestions'); if(sug) sug.innerHTML=(SEARCH_ONTOLOGY.examples||[]).slice(0,5).map(e=>`<button type="button" data-suggest="${esc2(e)}">${esc2(e)}</button>`).join('');}
function addResultBadges(parsed){document.querySelectorAll('.card').forEach(card=>{const id=card.dataset.id; const x=(typeof all!=='undefined'?all:[]).find(i=>String(i.id)===String(id)); if(!x)return; const body=card.querySelector('.body'); if(!body || body.querySelector('.matchReasons')) return; const rs=reasons(x,parsed); const div=document.createElement('div'); div.className='matchReasons'; div.innerHTML=rs.map(r=>`<span class="reason">${esc2(r)}</span>`).join(''); const geo=document.createElement('div'); geo.className='geoHint'; const level=x.geo_quality?.level || x.location_intelligence?.quality || 'source'; geo.textContent='Localisation '+level+' · prudente sauf adresse source'; const actions=body.querySelector('.actions'); body.insertBefore(div, actions); body.insertBefore(geo, actions);});}
function removeChip(i){state.q=''; if(typeof syncInputs==='function')syncInputs(); enhancedApply();}
const originalRender = typeof render==='function' ? render : null; const originalCard = typeof card==='function' ? card : null;
if(originalCard){ card=function(x){ let h=originalCard(x); return h.replace('<img loading="lazy"', '<img loading="lazy" decoding="async"').replace('<div class="actions">', `${(x.feature_tags||[]).slice(0,3).map(t=>`<span class="pill">${esc2(t)}</span>`).join('')}<div class="actions">`); }; }
function filterNaturalResults(parsed, relaxedLocation=false){let res=all.filter(x=>!hidden.has(x.id)); if(parsed.locations.length && !relaxedLocation) res=res.filter(x=>parsed.locations.some(l=>matchesLocation(x,l))); if(parsed.locations.length && relaxedLocation){const communes=[...new Set(parsed.locations.map(l=>l.commune).filter(Boolean).map(norm2))]; if(communes.length) res=res.filter(x=>communes.includes(norm2(x.city)) || communes.includes(norm2(x.location_intelligence?.commune_inferred||'')));}
 if(parsed.tokens.length) res=res.filter(x=>parsed.tokens.every(t=>(x._hay||'').includes(t))); if(parsed.rooms.length) res=res.filter(x=>parsed.rooms.includes(Number(x.rooms))); if(parsed.typeHint && !state.type) res=res.filter(x=>norm2(x.type).includes(parsed.typeHint)); if(parsed.amenities.length) res=res.filter(x=>parsed.amenities.every(a=>(x.feature_tags||[]).includes(a)||(x._hay||'').includes(norm2(a)))); if(parsed.excludeAmenities.includes('Meublé')) res=res.filter(x=>!isExplicitMeuble(x)); if(state.city) res=res.filter(x=>x.city===state.city); if(state.type) res=res.filter(x=>x.type===state.type); if(state.region) res=res.filter(x=>x.region===state.region); const maxPrice=state.maxPrice||parsed.maxPrice; if(maxPrice) res=res.filter(x=>x.price && x.price<=Number(maxPrice)); const minPrice=state.minPrice||parsed.minPrice; if(minPrice) res=res.filter(x=>x.price && x.price>=Number(minPrice)); if(parsed.priceOp==='equal' && parsed.barePrice) res=res.filter(x=>x.price && Math.abs(Number(x.price)-parsed.barePrice)<=25); if(parsed.priceOp==='around' && parsed.barePrice) res=res.filter(x=>x.price && Math.abs(Number(x.price)-parsed.barePrice)<=100); const minSurface=state.minSurface||parsed.minSurface; if(minSurface) res=res.filter(x=>x.surface && x.surface>=Number(minSurface)); if(state.minRooms) res=res.filter(x=>x.rooms && Number(x.rooms)>=Number(state.minRooms)); if(state.minBedrooms) res=res.filter(x=>x.bedrooms && Number(x.bedrooms)>=Number(state.minBedrooms)); if(state.minScore) res=res.filter(x=>Number(x.opportunity_score||x.score||0)>=Number(state.minScore)); if(state.zones){const zs=state.zones.split('|').map(norm2).filter(Boolean); if(zs.length) res=res.filter(x=>{const hay=norm2([x.city,x.district,x.location,x.title,x.description].join(' ')); return zs.some(z=>hay.includes(z));});} return res;}
function enhancedApply(updateUrl=true){const parsed=parseNatural(state.q); renderChips(parsed); let res=filterNaturalResults(parsed,false); let relaxed=false; if(res.length===0 && parsed.locations?.some(l=>l.specific&&l.commune)){res=filterNaturalResults(parsed,true); relaxed=res.length>0;} const s=state.sort; res.sort((a,b)=> s==='price_asc'?(a.price||9e9)-(b.price||9e9):s==='price_desc'?(b.price||0)-(a.price||0):s==='surface_desc'?(b.surface||0)-(a.surface||0):s==='recent'?String(b.seen_last_at||'').localeCompare(String(a.seen_last_at||'')):(relScore(b,parsed)*100+(b.opportunity_score||b.score||0))-(relScore(a,parsed)*100+(a.opportunity_score||a.score||0))); render(res); if(relaxed){const u=document.querySelector('#understood'); if(u) u.textContent+=' · Aucun exact quartier: affichage élargi à la commune source.';} addResultBadges(parsed); if(updateUrl) syncUrl(); return res;}
function choosePrice(op,v){v=Number(v); state.q=op==='max'?'moins '+v:op==='min'?'plus '+v:op==='equal'?'='+v:'autour de '+v; state.maxPrice=''; state.minPrice=''; if(typeof syncInputs==='function')syncInputs(); enhancedApply();}
apply = enhancedApply; parseQuery = parseNatural; relevance = (x,tokens)=>relScore(x, Array.isArray(tokens)?{tokens}:tokens);
ready(()=>{const q=document.querySelector('#q'); if(q) q.placeholder='Ex: F4 non meublé Beauséjour 900'; document.querySelector('#nlChips')?.addEventListener('click',e=>{const op=e.target.closest('[data-price-op]'); if(op){choosePrice(op.dataset.priceOp, op.dataset.priceValue); return;} const b=e.target.closest('[data-chip-remove]'); if(b) removeChip(b.dataset.chipRemove);}); document.querySelector('#suggestions')?.addEventListener('click',e=>{const b=e.target.closest('[data-suggest]'); if(!b)return; state.q=b.dataset.suggest; syncInputs(); enhancedApply();}); document.addEventListener('click',e=>{if(e.target.id==='relaxBudget'){const p=parseNatural(state.q); const v=Number(state.maxPrice||p.maxPrice||p.barePrice||0); if(v){state.maxPrice=String(v+100); syncInputs(); enhancedApply();}} if(e.target.id==='clearNatural'){state.q=''; syncInputs(); enhancedApply();}}); setTimeout(()=>{try{(typeof all!=='undefined'?all:[]).forEach(x=>{x._hay=itemHay(x)}); enhancedApply(false)}catch(e){console.error('natural search v2 apply failed',e)}}, 350);});
window.__naturalSearchV2={parseNatural, ontology:SEARCH_ONTOLOGY};
})();
</script>
'''

def norm_py(s: Any) -> str:
    import unicodedata
    s = unicodedata.normalize('NFD', str(s or ''))
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()

def uniq(seq: list[str]) -> list[str]:
    out=[]
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out

def feature_tags(item: dict[str, Any]) -> list[str]:
    text = norm_py(' '.join(str(item.get(k) or '') for k in ['title','description','location','city','district','type']))
    tags=[]
    analysis=item.get('description_analysis') or {}
    furnished=((((analysis.get('property_state') or {}).get('furnished') or {}).get('status')) or '')
    if furnished == 'meuble':
        tags.append('Meublé')
    for label, aliases in AMENITY_ALIASES.items():
        if any(norm_py(a) in text for a in aliases):
            tags.append(label)
    return uniq(tags)

def geo_quality(item: dict[str, Any]) -> dict[str, Any]:
    loc=item.get('location_intelligence') or {}
    q=norm_py(loc.get('quality') or '')
    city=item.get('city') or ''
    district=item.get('district') or ''
    if any(k in q for k in ['haute','high','quartier','adresse']): level,score='haute',0.8
    elif city and district and norm_py(city)!=norm_py(district): level,score='moyenne',0.65
    elif city: level,score='commune',0.45
    else: level,score='faible',0.15
    return {'level':level,'score':score,'note':'Localisation approximative sauf adresse explicitement fournie par la source.'}

def image_quality(item: dict[str, Any], app: Path) -> dict[str, Any]:
    raw_paths = [*(item.get('local_image_urls') or []), *([item.get('local_image_url')] if item.get('local_image_url') else [])]
    paths = uniq([str(x) for x in raw_paths if x])
    issues=[]; valid=0; bytes_=0
    for rel in paths[:24]:
        p=app/str(rel)
        if not p.exists(): issues.append('missing_file'); continue
        size=p.stat().st_size; bytes_ += size
        if size < 2048: issues.append('too_small')
        elif size > 900_000: issues.append('heavy_image')
        else: valid += 1
    return {'status':'ok' if valid and not issues else 'partial' if valid else 'no_valid_local_image', 'local_count':len(paths), 'valid_local_count':valid, 'bytes':bytes_, 'issues':sorted(set(issues))}

def enhance_json(app: Path) -> dict[str, Any]:
    p=app/'listings.json'
    data=json.loads(p.read_text(encoding='utf-8'))
    items=data.get('listings') or []
    for item in items:
        item['feature_tags']=feature_tags(item)
        item['geo_quality']=geo_quality(item)
        item['image_quality']=image_quality(item, app)
    p.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    ontology={'version':'natural_search_v2','generated_at':data.get('generated_at'),'specific_locations':SPECIFIC_LOCATION_ALIASES,'commune_aliases':COMMUNE_ALIASES,'amenity_aliases':AMENITY_ALIASES,'examples':EXAMPLES,'contract':['chips dynamiques de confirmation','type/ville/budget stricts','équipements/proximité en ranking','géographie prudente']}
    (app/'search_ontology.json').write_text(json.dumps(ontology, ensure_ascii=False, indent=2), encoding='utf-8')
    photo={'version':'photo_quality_v1','generated_at':data.get('generated_at'),'summary':{'listings':len(items),'with_local_primary':sum(1 for x in items if x.get('local_image_url')),'with_local_gallery':sum(1 for x in items if len(x.get('local_image_urls') or [])>1),'with_valid_local':sum(1 for x in items if (x.get('image_quality') or {}).get('valid_local_count')),'with_issues':sum(1 for x in items if (x.get('image_quality') or {}).get('issues'))},'issues':[{'id':x.get('id'),'source':x.get('source'),'title':x.get('title'),'image_quality':x.get('image_quality')} for x in items if (x.get('image_quality') or {}).get('issues') or not (x.get('image_quality') or {}).get('valid_local_count')][:300]}
    (app/'photo_quality.json').write_text(json.dumps(photo, ensure_ascii=False, indent=2), encoding='utf-8')
    cov_path=app/'coverage.json'
    if cov_path.exists():
        cov=json.loads(cov_path.read_text(encoding='utf-8'))
        cov['valid_local_images']=photo['summary']['with_valid_local']
        cov['photo_quality_issues']=photo['summary']['with_issues']
        cov['geo_confidence']={level:sum(1 for x in items if (x.get('geo_quality') or {}).get('level')==level) for level in ['haute','moyenne','commune','faible']}
        cov['amenity_tags']={tag:sum(1 for x in items if tag in (x.get('feature_tags') or [])) for tag in AMENITY_ALIASES}
        cov_path.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'listings':len(items), **photo['summary']}

def strip_inline_handlers(html_text: str) -> str:
    # Side pages are static. Inline handlers weaken CSP and are hard to audit.
    # We strip them rather than permitting them; broken images still show their alt/fallback layout.
    html_text = re.sub(r'\s+on[a-z]+\s*=\s*"[^"]*"', '', html_text, flags=re.I)
    html_text = re.sub(r"\s+on[a-z]+\s*=\s*'[^']*'", '', html_text, flags=re.I)
    return html_text


def enhance_html(app: Path) -> None:
    p=app/'index.html'
    text=p.read_text(encoding='utf-8')
    text=re.sub(r'<script id="naturalSearchV2">.*?</script>\s*', '', text, flags=re.S)
    text=text.replace(CSS, '')
    if 'class="searchWrap"' not in text:
        text=text.replace('<div class="search"><input id="q" placeholder="Ville, quartier, critères… ex: Sainte-Marie T2 1000"/><button id="searchBtn">Rechercher</button></div>', '<div class="searchWrap"><div class="search"><input id="q" placeholder="Ex: T2 Saint-Denis moins 900€ meublé parking" aria-describedby="understood"/><button id="searchBtn">Rechercher</button></div><div class="searchFeedback" aria-live="polite"><div class="nlChips" id="nlChips"></div><div class="understood" id="understood">Tape une recherche naturelle : budget, ville, type, quartier, parking, meublé…</div><div class="suggestions" id="suggestions"></div></div></div>')
    if '.searchWrap{' not in text:
        text=text.replace('</style>', CSS+'\n</style>', 1)
    ontology={'specific_locations':SPECIFIC_LOCATION_ALIASES,'commune_aliases':COMMUNE_ALIASES,'amenity_aliases':AMENITY_ALIASES,'examples':EXAMPLES}
    js=JS_TEMPLATE.replace('__ONTOLOGY__', json.dumps(ontology, ensure_ascii=False))
    text=text.replace('</body></html>', js+'\n</body></html>')
    p.write_text(text, encoding='utf-8')

    for name in ['changes.html', 'veille.html']:
        side = app / name
        if side.exists():
            cleaned = strip_inline_handlers(side.read_text(encoding='utf-8', errors='replace'))
            side.write_text(cleaned, encoding='utf-8')

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--app', default='artifacts/app')
    args=ap.parse_args()
    app=Path(args.app).resolve()
    if not (app/'index.html').exists() or not (app/'listings.json').exists():
        raise SystemExit(f'missing index/listings in {app}')
    stats=enhance_json(app)
    enhance_html(app)
    print(json.dumps({'ok':True,'app':str(app),'stats':stats}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
