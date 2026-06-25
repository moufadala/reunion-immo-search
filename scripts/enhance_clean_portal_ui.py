#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path

APP = Path('/opt/data/projects/reunion-immo-search/artifacts/app')
for html_path in [APP/'index.html', Path('/opt/data/projects/reunion-immo-search/artifacts/app_clean_v1/index.html')]:
    if not html_path.exists():
        continue
    s = html_path.read_text(encoding='utf-8')
    if "href=\"changes.html\"" not in s:
        s = s.replace(
            '<button class="btn secondary" id="resetBtn">Réinitialiser</button></div></header>',
            '<button class="btn secondary" id="resetBtn">Réinitialiser</button><a class="btn secondary" href="changes.html">Veille</a><a class="btn secondary" href="alertes_cours.html">Cours alertes</a></div></header>'
        )
    if "data-analyze=" not in s:
        s = s.replace(
            '<div class="actions"><a href="${x.url}" target="_blank" rel="noreferrer" onclick="event.stopPropagation()">Source</a><button data-hide="${x.id}" onclick="event.stopPropagation()">Masquer</button></div></div></article>`}',
            '<div class="actions"><a href="${x.url}" target="_blank" rel="noreferrer" onclick="event.stopPropagation()">Source</a><button data-analyze="${x.id}" onclick="event.stopPropagation()">Analyse</button><button data-hide="${x.id}" onclick="event.stopPropagation()">Masquer</button></div></div></article>`}'
        )
    if "function openAnalysis" not in s:
        marker = "function openDetail(id){const x=all.find(i=>i.id===id); if(!x)return;"
        insert = r"""
function openAnalysis(id){const x=all.find(i=>i.id===id); if(!x)return; const a=x.opportunity_analysis||{}; const loc=x.location_intelligence||{}; const photo=x.photo_status||{}; const src=imgSrc(x); $('#mHead').textContent='Analyse opportunité · '+(x.source||'source'); $('#mPrice').textContent=(a.score!=null? a.score+'/100 · ':'')+(a.label||'Analyse à vérifier'); $('#mLoc').textContent=(x.location||x.city||'Localisation n.c.')+' · précision '+(loc.quality||'n.c.'); $('#mTitle').textContent=x.title; $('#mImg').innerHTML=src?`<img src="${src}" alt="${x.title.replaceAll('"','&quot;')}">`:'<div class="no-photo">Photo indisponible</div>'; const ppm=a.price_per_m2?`<span class="pill">${a.price_per_m2} €/m²</span>`:''; const ref=a.sector_median_price_per_m2?`<span class="pill">médiane secteur ${a.sector_median_price_per_m2} €/m²</span>`:''; const dedup=x.dedup_group_id?`<span class="pill">doublon probable ${x.dedup_group_id}</span>`:''; $('#mMeta').innerHTML=`<span class="pill">${x.type||'Bien'}</span>${ppm}${ref}${dedup}<span class="pill">photo ${photo.origin||'n.c.'} · ${photo.local_count||0} locale(s)</span>`; const reasons=(a.reasons||[]).map(r=>`<li>${r}</li>`).join('')||'<li>Pas de signal fort.</li>'; const warnings=(a.warnings||[]).map(r=>`<li>${r}</li>`).join('')||'<li>Aucun avertissement majeur.</li>'; $('#mDesc').innerHTML=`<b>Pourquoi</b><ul>${reasons}</ul><b>À vérifier</b><ul>${warnings}</ul><p class="muted">Méthode: ${a.method||'prix/m², complétude, photos, doublons et signaux faibles.'}</p>`; $('#mTrust').textContent=`Localisation: ${loc.commune_inferred||x.city||'n.c.'} / ${loc.region_inferred||x.region||'n.c.'}; méthode ${loc.method||'n.c.'}.`; $('#mSource').href=x.url; $('#mFav').textContent=favs.has(x.id)?'Retirer favori':'Ajouter favori'; $('#mFav').onclick=()=>{favs.has(x.id)?favs.delete(x.id):favs.add(x.id);saveFavs();openAnalysis(id);apply();}; $('#modal').classList.add('open');}
"""
        s = s.replace(marker, insert + marker)
    if "const analyze=e.target.closest('[data-analyze]');" not in s:
        s = s.replace(
            "$('#grid').onclick=e=>{const fav=e.target.closest('[data-fav]');",
            "$('#grid').onclick=e=>{const analyze=e.target.closest('[data-analyze]'); if(analyze){e.stopPropagation(); openAnalysis(analyze.dataset.analyze); return;} const fav=e.target.closest('[data-fav]');"
        )
    html_path.write_text(s, encoding='utf-8')
    print('ENHANCED', html_path)
