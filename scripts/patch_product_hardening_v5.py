#!/usr/bin/env python3
"""Product hardening v5 for the public clean immo portal.

Fixes the natural search contract without relying on the older stacked v2/v3 snippets:
- exact quartier searches stay exact (no silent commune-wide fallback);
- bare budget numbers render an explicit operator chooser with stable data-price-op attrs;
- operator clicks update #q and the understood line;
- chips/cards are regenerated from one parser/filter path.
"""
from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime, UTC
from pathlib import Path

PROJECT = Path('/opt/data/projects/reunion-immo-search')
DEFAULT_APP = PROJECT / 'artifacts/app'

CSS = r'''
/* Product hardening v5: explicit natural-search operator chooser + strict quartier matching */
.priceChoice button{border:1px solid #f4d38d;background:#fffdf6;color:#7c4a03;font-weight:950;border-radius:999px;padding:5px 9px;margin-left:4px;min-width:32px;min-height:32px}.priceChoice button:active{transform:translateY(1px)}
@media(max-width:580px){.top,.bar{max-width:100vw;overflow-x:hidden}.bar{padding:9px 11px}.logo{margin-bottom:0}.searchWrap{min-width:0!important;width:100%!important}.search{width:100%!important;min-width:0!important}.search input{min-width:0;width:100%}.stat{min-width:0}.nlChips{justify-content:flex-start}.priceChoice{display:flex!important;flex-wrap:wrap;gap:4px;max-width:100%;white-space:normal}.priceChoice button{min-width:32px!important;min-height:32px!important;margin-left:0!important}}
.searchFeedback .understood strong{color:#134e4a}.nlchip.intent-warn{background:#fff8e6;border-color:#f4d38d;color:#7c4a03}.empty .examples{margin-top:8px;font-size:13px}
.searchFeedback{margin:-8px 0 12px;padding:0 2px}.nlChips{display:flex;gap:7px;flex-wrap:wrap;min-height:0}.nlchip{display:inline-flex;align-items:center;gap:6px;border:1px solid #cfe7df;background:#effaf6;color:#134e4a;border-radius:999px;padding:6px 10px;font-size:12px;font-weight:820;box-shadow:0 2px 8px rgba(15,118,110,.06)}.nlchip.soft{background:#fff8e6;border-color:#f4d38d;color:#7c4a03}.nlchip button{border:0;background:transparent;color:inherit;font-weight:900;cursor:pointer;padding:0 1px}.understood{margin-top:6px;color:var(--muted);font-size:13px}.suggestions{display:flex;gap:7px;overflow:auto;padding-top:7px}
.loadMoreWrap{display:flex;justify-content:center;margin:18px 0 8px}#loadMore{min-height:46px!important;min-width:190px!important;padding:12px 18px!important;border-radius:999px!important;font-weight:850!important;line-height:1.2!important}
.pill+.pill{margin-left:4px}
@media(max-width:580px){.searchFeedback{margin:0 0 8px;padding:0 2px}.nlChips{flex-wrap:nowrap;overflow:auto;padding-bottom:3px}.nlchip{white-space:nowrap;min-height:36px}.understood{font-size:12px;line-height:1.25}.suggestions{display:none}.card .statusStrip{display:none}}
'''

SCRIPT = r'''
<script id="productHardeningV5">
(function(){
function n5(s){return String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9<>:=]+/g,' ').trim().replace(/\s+/g,' ');}
function e5(s){return (s??'').toString().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
const LOCS5=[
 {label:'Rivière des Pluies', commune:'Sainte-Marie', exact:true, aliases:['riviere des pluies','rivieres des pluies','rivière des pluies','rivières des pluies']},
 {label:'Beauséjour', commune:'Sainte-Marie', exact:true, aliases:['beausejour','beauséjour']},
 {label:'Grande Montée', commune:'Sainte-Marie', exact:true, aliases:['grande montee','grande montée','la grande montee','la grande montée']},
 {label:'Duparc', commune:'Sainte-Marie', exact:true, aliases:['duparc']},
 {label:'La Bretagne', commune:'Saint-Denis', exact:true, aliases:['bretagne','la bretagne']},
 {label:'Moufia', commune:'Saint-Denis', exact:true, aliases:['moufia']},
 {label:'Camélias', commune:'Saint-Denis', exact:true, aliases:['camelias','camélias','les camelias','les camélias']},
 {label:'Domenjod', commune:'Saint-Denis', exact:true, aliases:['domenjod']},
 {label:'Chaudron', commune:'Saint-Denis', exact:true, aliases:['chaudron','le chaudron']},
 {label:'Bellepierre', commune:'Saint-Denis', exact:true, aliases:['bellepierre']},
 {label:'Montgaillard', commune:'Saint-Denis', exact:true, aliases:['montgaillard']},
 {label:'La Source Saint-Denis', commune:'Saint-Denis', exact:true, aliases:['quartier la source saint denis','la source saint denis']},
 {label:'Bois de Nèfles Sainte-Clotilde', commune:'Saint-Denis', exact:true, aliases:['bois de nefles sainte clotilde','bois de nèfles sainte-clotilde','ste clotilde bois de nefles','sainte clotilde bois de nefles']},
 {label:'Bois de Nèfles Saint-Paul', commune:'Saint-Paul', exact:true, aliases:['bois de nefles saint paul','bois de nèfles saint-paul','bois de nefles st paul']},
 {label:'Sainte-Clotilde', commune:'Saint-Denis', exact:true, aliases:['sainte clotilde','ste clotilde','ste-clotilde']},
 {label:'Sainte-Marie', commune:'Sainte-Marie', exact:false, aliases:['sainte marie','ste marie','ste-marie']},
 {label:'Saint-Denis', commune:'Saint-Denis', exact:false, aliases:['saint denis','st denis','st-denis']},
 {label:'Saint-Pierre', commune:'Saint-Pierre', exact:false, aliases:['saint pierre','st pierre','st-pierre']},
 {label:'Saint-Paul', commune:'Saint-Paul', exact:false, aliases:['saint paul','st paul','st-paul']},
 {label:'Le Tampon', commune:'Le Tampon', exact:false, aliases:['le tampon','tampon']},
 {label:'Saint-Leu', commune:'Saint-Leu', exact:false, aliases:['saint leu','st leu','st-leu']},
 {label:'Saint-Louis', commune:'Saint-Louis', exact:false, aliases:['saint louis','st louis','st-louis']},
 {label:'Saint-Joseph', commune:'Saint-Joseph', exact:false, aliases:['saint joseph','st joseph','st-joseph']},
 {label:'Petite-Île', commune:'Petite-Île', exact:false, aliases:['petite ile','petite-ile','petite île']},
 {label:'La Possession', commune:'La Possession', exact:false, aliases:['la possession','possession']},
 {label:'La Montagne', commune:'Saint-Denis', exact:true, aliases:['la montagne']},
 {label:'Sainte-Suzanne', commune:'Sainte-Suzanne', exact:false, aliases:['sainte suzanne','ste suzanne','quartier francais','quartier français']},
 {label:'Saint-André', commune:'Saint-André', exact:false, aliases:['saint andre','saint andré','st andre']},
 {label:'La Convenance', commune:'Sainte-Marie', exact:true, aliases:['la convenance','convenance']},
 {label:'Les Cafés', commune:'Sainte-Marie', exact:true, aliases:['les cafés','les cafes','cafés','cafes']},
 {label:'Bagatelle', commune:'Sainte-Suzanne', exact:true, aliases:['bagatelle']},
 {label:'Deux Rives', commune:'Sainte-Suzanne', exact:true, aliases:['deux rives','les deux rives']},
 {label:'Cambuston', commune:'Saint-André', exact:true, aliases:['cambuston']},
 {label:'Champ Borne', commune:'Saint-André', exact:true, aliases:['champ borne']},
 {label:'Ravine Creuse', commune:'Saint-André', exact:true, aliases:['ravine creuse']},
 {label:'Étang-Salé', commune:'Étang-Salé', exact:false, aliases:['etang sale','étang salé','etang-sale','étang-salé']},
 {label:'Trois-Bassins', commune:'Trois-Bassins', exact:false, aliases:['trois bassins','trois-bassins']},
 {label:'La Saline les Bains', commune:'Saint-Paul', exact:true, aliases:['la saline les bains','la-saline-les-bains','saline les bains']},
 {label:'Zone non précisée', commune:'', exact:true, aliases:['zone non precisee','zone non précisée','non precisee','non précisée']}
].map(l=>({...l, aliases:[l.label,...l.aliases].map(n5)})).sort((a,b)=>Math.max(...b.aliases.map(x=>x.length))-Math.max(...a.aliases.map(x=>x.length)));
const AMENITIES5={Parking:['parking','garage','stationnement','place de parking'], 'Varangue / terrasse':['varangue','terrasse','balcon'], Jardin:['jardin'], Climatisation:['clim','climatisation'], Ascenseur:['ascenseur'], Piscine:['piscine']};
function chip5(type,label,value,soft=false){return {type,label,value:value??label,soft};}
function parse5(q){let raw=n5(q).replace(/\bst\b/g,'saint').replace(/\bste\b/g,'sainte'); let work=' '+raw+' '; const chips=[], rooms=[], amenities=[], excludes=[], locations=[]; let furnished=false, typeHint='', minSurface=null, minPrice=null, maxPrice=null, priceOp='', barePrice=null;
 [...work.matchAll(/\b[tf]\s*([1-9])\b/g)].forEach(m=>{const v=Number(m[1]); if(!rooms.includes(v)){rooms.push(v);chips.push(chip5('rooms','T/F'+v,v));} work=work.replace(m[0],' ');});
 [...work.matchAll(/\b([1-9])\s*(?:piece|pieces|p)\b/g)].forEach(m=>{const v=Number(m[1]); if(!rooms.includes(v)){rooms.push(v);chips.push(chip5('rooms','T/F'+v,v));} work=work.replace(m[0],' ');});
 if(/\bstudio\b/.test(work)){rooms.push(1); chips.push(chip5('rooms','Studio / T1',1)); work=work.replace(/\bstudio\b/g,' ');}
 const between=work.match(/(?:entre|de)\s*(\d{3,5})\s*(?:et|a|à|-)\s*(\d{3,5})/); if(between){minPrice=Number(between[1]);maxPrice=Number(between[2]);priceOp='range';chips.push(chip5('budget','Entre '+minPrice+' et '+maxPrice+' €',{min:minPrice,max:maxPrice}));work=work.replace(between[0],' ');}
 const pmin=work.match(/(?:plus de|plus|au dessus de|min|minimum|>=|>)\s*(\d{2,5})/); if(pmin&&!priceOp){minPrice=Number(pmin[1]);priceOp='min';chips.push(chip5('budget','≥ '+minPrice+' €',minPrice));work=work.replace(pmin[0],' ');}
 const pmax=work.match(/(?:moins de|moins|sous|max|budget max|loyer max|jusqu a|jusqua|<=|<)\s*(\d{2,5})/); if(pmax&&!priceOp){maxPrice=Number(pmax[1]);priceOp='max';chips.push(chip5('budget','≤ '+maxPrice+' €',maxPrice));work=work.replace(pmax[0],' ');}
 const peq=work.match(/(?:=|exact|exactement|egal|égal)\s*(\d{3,5})|(\d{3,5})\s*(?:exact|exactement|egal|égal)/); if(peq&&!priceOp){barePrice=Number(peq[1]||peq[2]);priceOp='equal';chips.push(chip5('budget','= '+barePrice+' €',barePrice));work=work.replace(peq[0],' ');}
 const paround=work.match(/(?:autour de|environ|vers|aux alentours de)\s*(\d{3,5})|(\d{3,5})\s*(?:environ|autour)/); if(paround&&!priceOp){barePrice=Number(paround[1]||paround[2]);priceOp='around';chips.push(chip5('budget','Autour de '+barePrice+' €',barePrice));work=work.replace(paround[0],' ');}
 if(!priceOp){const nums=[...work.matchAll(/\b(\d{3,5})\s*(?:€|eur|euros)?\b/g)].map(m=>Number(m[1])).filter(v=>v>=300&&v<=10000); if(nums.length){barePrice=Math.max(...nums);}}
 const surf=work.match(/(\d{1,3})\s*(?:m2|m²|metres|metre|m)\b/); if(surf){minSurface=Number(surf[1]); chips.push(chip5('surface','≥ '+minSurface+' m²',minSurface)); work=work.replace(surf[0],' ');}
 if(/\b(maison|villa)\b/.test(work)){typeHint='maison';chips.push(chip5('type','Maison / villa','maison'));work=work.replace(/\b(maison|villa)\b/g,' ');} if(/\b(appartement|appart|apt)\b/.test(work)){typeHint='appartement';chips.push(chip5('type','Appartement','appartement'));work=work.replace(/\b(appartement|appart|apt)\b/g,' ');}
 for(const l of LOCS5){const hit=l.aliases.find(al=>al.length>2 && work.includes(' '+al+' ')); if(hit){locations.push(l);chips.push(chip5('location',l.label,l.label)); for(const al of l.aliases){work=work.replaceAll(' '+al+' ',' ');}}}
 if(/\b(non|pas|sans)\s+meubl[ée]e?\b|\blocation nue\b|\bloue vide\b|\bvide\b/.test(work)){excludes.push('Meublé');chips.push(chip5('exclude','Non meublé','Meublé'));work=work.replace(/\b(non|pas|sans)\s+meubl[ée]e?\b|\blocation nue\b|\bloue vide\b|\bvide\b/g,' ');}
 else if(/\bmeubl[ée]e?\b/.test(work)){furnished=true;chips.push(chip5('furnished','Meublé','Meublé'));work=work.replace(/\bmeubl[ée]e?\b/g,' ');}
 for(const [label,aliases] of Object.entries(AMENITIES5)){for(const al of aliases.map(n5)){if(work.includes(' '+al+' ')){amenities.push(label);chips.push(chip5('amenity',label,label));work=work.replaceAll(' '+al+' ',' ');break;}}}
 const stop=new Set('le la les l un une des du de d a au aux en sur dans avec sans et ou pour moins sous max budget loyer jusqu jusqua entre proche pres près vers autour m2 m metres metre min minimum plus piece pieces p ch chambre chambres euro euros eur meuble meublee non pas'.split(' '));
 const tokens=[...new Set(work.split(/\s+/).filter(Boolean).filter(x=>!stop.has(x)&&!/^[tf][1-9]$/.test(x)&&!/^\d{1,5}$/.test(x)))]; return {raw,tokens,rooms,maxPrice,minPrice,barePrice,priceOp,minSurface,typeHint,furnished,amenities:[...new Set(amenities)],excludes:[...new Set(excludes)],locations,chips};}
function hay5(x){return x._hay5||n5([x.id,x.source_id,x.title,x.city,x.district,x.location,x.region,x.type,x.source,x.agency,x.description,x.rooms?('t'+x.rooms+' f'+x.rooms+' '+x.rooms+' pieces'):'',x.bedrooms?x.bedrooms+' chambres':'',x.furnished||'',(x.feature_tags||[]).join(' '),x.location_intelligence?.district_best||'',x.location_intelligence?.precise_location_label||'',x.location_intelligence?.commune_inferred||''].join(' '));}
function exactLoc5(x,l){const h=hay5(x); return l.aliases.some(a=>a && h.includes(a)) || (!l.exact && n5(l.label)===n5(l.commune) && (n5(x.city)===n5(l.commune)||n5(x.location_intelligence?.commune_inferred)===n5(l.commune)));}
function explicitMeuble5(x){const st=x.description_analysis?.property_state?.furnished?.status; if(st==='non_meuble')return false; if(st==='meuble')return true; const f=n5(x.furnished||''); const tags=(x.feature_tags||[]).map(n5); return ['meuble','meublee'].includes(f)||tags.includes('meuble')||tags.includes('meublee');}
function score5(x,p){let s=0,h=hay5(x); for(const t of p.tokens){s+=h.split(' ').includes(t)?8:(h.includes(t)?3:0);} for(const l of p.locations){if(exactLoc5(x,l))s+=60;} for(const a of p.amenities){if((x.feature_tags||[]).includes(a)||h.includes(n5(a)))s+=10;} return s;}
function displayVisible5(x){return x.display_canonical!==false;}
function filter5(p){let res=all.filter(x=>!hidden.has(x.id)&&displayVisible5(x)); if(p.locations.length)res=res.filter(x=>p.locations.some(l=>exactLoc5(x,l))); if(p.tokens.length)res=res.filter(x=>p.tokens.every(t=>hay5(x).includes(t))); if(p.rooms.length)res=res.filter(x=>p.rooms.includes(Number(x.rooms))); if(p.typeHint&&!state.type)res=res.filter(x=>n5(x.type).includes(p.typeHint)); if(p.amenities.length)res=res.filter(x=>p.amenities.every(a=>(x.feature_tags||[]).includes(a)||hay5(x).includes(n5(a)))); if(p.excludes.includes('Meublé'))res=res.filter(x=>!explicitMeuble5(x)); if(p.furnished)res=res.filter(x=>explicitMeuble5(x)); if(state.city)res=res.filter(x=>x.city===state.city); if(state.type)res=res.filter(x=>x.type===state.type); if(state.region)res=res.filter(x=>x.region===state.region); if(state.maxPrice)res=res.filter(x=>x.price&&x.price<=Number(state.maxPrice)); if(state.minPrice)res=res.filter(x=>x.price&&x.price>=Number(state.minPrice)); if(p.maxPrice)res=res.filter(x=>x.price&&x.price<=p.maxPrice); if(p.minPrice)res=res.filter(x=>x.price&&x.price>=p.minPrice); if(p.priceOp==='equal'&&p.barePrice)res=res.filter(x=>x.price&&Math.abs(Number(x.price)-p.barePrice)<=25); if(p.priceOp==='around'&&p.barePrice)res=res.filter(x=>x.price&&Math.abs(Number(x.price)-p.barePrice)<=100); const ms=state.minSurface||p.minSurface; if(ms)res=res.filter(x=>x.surface&&x.surface>=Number(ms)); if(state.minRooms)res=res.filter(x=>x.rooms&&Number(x.rooms)>=Number(state.minRooms)); return res;}
function priceOps5(p){if(!p.barePrice||p.priceOp)return ''; const v=p.barePrice; return `<span class="nlchip soft intent-warn priceChoice"><span>${v} € ?</span><button data-op="max" data-price-op="max" data-price-value="${v}" aria-label="Budget inférieur ou égal à ${v} euros">≤</button><button data-op="min" data-price-op="min" data-price-value="${v}" aria-label="Budget supérieur ou égal à ${v} euros">≥</button><button data-op="equal" data-price-op="equal" data-price-value="${v}" aria-label="Budget égal à ${v} euros">=</button><button data-op="around" data-price-op="around" data-price-value="${v}" aria-label="Budget autour de ${v} euros">autour</button></span>`;}
function understood5(p,msg){if(msg)return msg; const bits=[]; for(const c of p.chips||[]) bits.push(c.label); if(p.barePrice&&!p.priceOp) bits.push('budget '+p.barePrice+' € à préciser'); return bits.length?'Critères compris : '+bits.join(' · '):'Tape une recherche naturelle : F4 non meublé Beauséjour 900, moins 900, autour de 1000…';}
function renderChips5(p,msg){const root=document.querySelector('#nlChips'); if(root)root.innerHTML=(p.chips||[]).map((c,i)=>`<span class="nlchip ${c.soft?'soft':''}">${e5(c.label)}<button type="button" data-v5-remove="${i}" title="Retirer">×</button></span>`).join('')+priceOps5(p); const u=document.querySelector('#understood'); if(u)u.textContent=understood5(p,msg); const sug=document.querySelector('#suggestions'); if(sug)sug.innerHTML='';}
function reasons5(x,p){const out=[]; if(p.rooms.includes(Number(x.rooms)))out.push('T/F'+x.rooms); for(const l of p.locations){if(exactLoc5(x,l))out.push(l.label);} if(p.excludes.includes('Meublé')&&!explicitMeuble5(x))out.push('non meublé'); if(p.maxPrice&&x.price<=p.maxPrice)out.push('≤ budget'); if(p.minPrice&&x.price>=p.minPrice)out.push('≥ budget'); return out.slice(0,5);}
function addBadges5(p){document.querySelectorAll('.card').forEach(c=>{c.querySelector('.matchReasons')?.remove(); c.querySelector('.geoHint')?.remove(); const x=all.find(i=>String(i.id)===String(c.dataset.id)); const body=c.querySelector('.body'), actions=c.querySelector('.actions'); if(!x||!body||!actions)return; const rs=reasons5(x,p); if(rs.length){const div=document.createElement('div');div.className='matchReasons';div.innerHTML=rs.map(r=>`<span class="reason">${e5(r)}</span>`).join('');body.insertBefore(div,actions);} });}
function explainZero5(p){
 const crit=(p.chips||[]).map(c=>c.label); if(p.barePrice&&!p.priceOp)crit.push('budget '+p.barePrice+' € à préciser');
 if(!crit.length)return '';
 const parts=[];
 for(const l of p.locations||[]){parts.push(`${l.label}: ${all.filter(x=>!hidden.has(x.id)&&exactLoc5(x,l)).length}`);}
 for(const r of p.rooms||[]){parts.push(`T/F${r}: ${all.filter(x=>!hidden.has(x.id)&&Number(x.rooms)===Number(r)).length}`);}
 if(p.excludes.includes('Meublé'))parts.push(`non meublé: ${all.filter(x=>!hidden.has(x.id)&&!explicitMeuble5(x)).length}`);
 if(p.furnished)parts.push(`meublé: ${all.filter(x=>!hidden.has(x.id)&&explicitMeuble5(x)).length}`);
 if(p.maxPrice)parts.push(`≤ ${p.maxPrice} €: ${all.filter(x=>!hidden.has(x.id)&&x.price&&x.price<=p.maxPrice).length}`);
 if(p.minPrice)parts.push(`≥ ${p.minPrice} €: ${all.filter(x=>!hidden.has(x.id)&&x.price&&x.price>=p.minPrice).length}`);
 if(p.priceOp==='equal'&&p.barePrice)parts.push(`= ${p.barePrice} €: ${all.filter(x=>!hidden.has(x.id)&&x.price&&Math.abs(Number(x.price)-p.barePrice)<=25).length}`);
 if(p.priceOp==='around'&&p.barePrice)parts.push(`autour ${p.barePrice} €: ${all.filter(x=>!hidden.has(x.id)&&x.price&&Math.abs(Number(x.price)-p.barePrice)<=100).length}`);
 const detail=parts.length?' Détail isolé — '+parts.join(' · ')+'.':'';
 return 'Aucune annonce ne correspond à la combinaison : '+crit.join(' · ')+'.'+detail+' Retire ou assouplis un critère.';
}
function apply5(updateUrl=true){const p=parse5(state.q); let res=filter5(p), msg=''; if(res.length===0){msg=explainZero5(p); if(!msg&&p.locations.length){const labels=p.locations.map(l=>l.label).join(', '); msg='Aucun résultat exact pour '+labels+'. Je n’élargis pas automatiquement au reste de la commune pour éviter les faux positifs.';}}
 const s=state.sort; res.sort((a,b)=>s==='price_asc'?(a.price||9e9)-(b.price||9e9):s==='price_desc'?(b.price||0)-(a.price||0):s==='surface_desc'?(b.surface||0)-(a.surface||0):s==='recent'?String(b.seen_last_at||'').localeCompare(String(a.seen_last_at||'')):(score5(b,p)*100+(b.opportunity_score||b.score||0))-(score5(a,p)*100+(a.opportunity_score||a.score||0))); render(res); renderChips5(p,msg); addBadges5(p); if(updateUrl)syncUrl(); window.__searchV5Last={parsed:p,count:res.length}; return res;}
function replacePrice5(q,v,op){const word=op==='max'?'moins ':op==='min'?'plus ':op==='equal'?'= ':'autour de '; const repl=word+v; let s=String(q||''); const phrase=/(moins de|moins|sous|max|budget max|loyer max|jusqu a|jusqua|plus de|plus|au dessus de|min|minimum|autour de|environ|vers|=|exactement|egal|égal)\s*\d{3,5}/i; if(phrase.test(s))return s.replace(phrase,repl); const bare=new RegExp('(^|\\s)'+v+'(?=\\s|€|eur|euros|$)','i'); if(bare.test(s))return s.replace(bare,(m,p)=>p+repl); return (s.trim()+' '+repl).trim();}
function stripByType5(q,c){let s=' '+String(q||' ')+' '; if(c.type==='budget')s=s.replace(/\b(entre|de)\s*\d{3,5}\s*(et|a|à|-)\s*\d{3,5}\b/ig,' ').replace(/\b(moins de|moins|sous|max|budget max|loyer max|jusqu a|jusqua|plus de|plus|au dessus de|min|minimum|autour de|environ|vers|=|exactement|egal|égal)\s*\d{3,5}\b/ig,' ').replace(/\b\d{3,5}\s*(€|eur|euros)?\b/ig,' ');
 else if(c.type==='rooms')s=s.replace(/\b[tf]\s*[1-9]\b/ig,' ').replace(/\bstudio\b/ig,' '); else if(c.type==='exclude')s=s.replace(/\b(non|pas|sans)\s+meubl[ée]e?\b|\blocation nue\b|\bloue vide\b/ig,' '); else if(c.type==='location'){const l=LOCS5.find(x=>x.label===c.value); if(l)for(const al of [l.label,...l.aliases])s=s.replace(new RegExp('\\b'+al.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b','ig'),' ');} else if(c.type==='type')s=s.replace(/\b(maison|villa|appartement|appart|apt)\b/ig,' '); else if(c.type==='amenity')s=s.replace(new RegExp('\\b'+String(c.value).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b','ig'),' '); return s.replace(/\s+/g,' ').trim();}
apply=apply5; parseQuery=parse5; window.__searchUXV5={parse5,apply5,replacePrice5};
document.querySelector('#nlChips')?.addEventListener('click',function(ev){const po=ev.target.closest('[data-price-op],[data-v3-price]'); const rm=ev.target.closest('[data-v5-remove]'); if(!po&&!rm)return; ev.preventDefault(); ev.stopImmediatePropagation(); const p=parse5(state.q); if(po){state.q=replacePrice5(state.q,po.dataset.priceValue||po.dataset.v,po.dataset.priceOp||po.dataset.v3Price);} if(rm){const c=p.chips[Number(rm.dataset.v5Remove)]; state.q=stripByType5(state.q,c);} syncInputs(); apply5();}, true);
setTimeout(()=>{try{(window.all||all||[]).forEach(x=>{x._hay5=hay5(x)}); apply5(false);}catch(err){console.error('product hardening v5 failed',err)}},950);
})();
</script>
'''


def patch_app(app: Path) -> dict:
    index = app / 'index.html'
    if not index.exists():
        raise FileNotFoundError(index)
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    backup = app.parent / f'{app.name}.bak.product-hardening-v5-{stamp}'
    if not backup.exists():
        shutil.copytree(app, backup)
    html = index.read_text(encoding='utf-8')
    html = html.replace(
        'V1 propre façon portail classique : chercher, filtrer, comparer, ouvrir la source. Pas de debug, pas de fouillis. Les photos principales locales sont utilisées quand elles existent.',
        'Portail propre avec recherche naturelle : tape un quartier, un budget, F4/T4, meublé/non meublé, puis vérifie les critères compris avant d’ouvrir la source.'
    )
    html = re.sub(r'\n<script id="productHardeningV5">.*?</script>\n', '\n', html, flags=re.S)
    html = re.sub(r'\n/\* Product hardening v5:.*?\.pill\+\.pill\{margin-left:4px\}\n', '\n', html, flags=re.S)
    # build_clean_portal_v1 can emit only the classic filter chips (#chips).
    # The natural-search hardening layer and browser audits need a stable
    # feedback contract: #nlChips for parsed chips, #understood for the human
    # explanation line, and #suggestions for future safe hints.  Older public
    # artifacts had this block from a previous patch; fresh daily candidates did
    # not, so the replay could publish a page where productHardeningV5 silently
    # had no visible state and the user-search audit timed out on #understood.
    if 'id="understood"' not in html:
        feedback = (
            '<div class="searchFeedback" aria-live="polite">'
            '<div class="nlChips" id="nlChips"></div>'
            '<div class="understood" id="understood">'
            'Tape une recherche naturelle : F4 non meublé Beauséjour 900, moins 900, autour de 1000…'
            '</div>'
            '<div class="suggestions" id="suggestions"></div>'
            '</div>'
        )
        scrim = '<div class="filterScrim" id="filterScrim" hidden></div>'
        if scrim in html:
            html = html.replace(scrim, scrim + feedback, 1)
        elif '<div class="chips" id="chips"></div>' in html:
            html = html.replace('<div class="chips" id="chips"></div>', '<div class="chips" id="chips"></div>' + feedback, 1)
        else:
            html = html.replace('<div class="toolbar">', feedback + '<div class="toolbar">', 1)
    html = html.replace('</style>', CSS + '</style>')
    html = html.replace('</body></html>', SCRIPT + '\n</body></html>')
    index.write_text(html, encoding='utf-8')
    return {'ok': True, 'app': str(app), 'backup': str(backup), 'index': str(index)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default=str(DEFAULT_APP))
    args = ap.parse_args()
    print(patch_app(Path(args.app)))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
