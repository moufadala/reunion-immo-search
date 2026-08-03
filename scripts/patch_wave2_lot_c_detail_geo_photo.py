#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
from pathlib import Path

ROOT = Path('/opt/data/projects/reunion-immo-search')
APP = ROOT / 'artifacts' / 'app'
INDEX = APP / 'index.html'
TS = dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
BAK = APP.parent / f'app.bak.wave2-lot-c-detail-geo-photo-{TS}'

CSS = r'''
/* Wave 2 Lot C: detail-only gallery/fallback + prudent geo wording */
.detailGeoNotice{margin-top:10px;border:1px solid #d7e5ff;background:#eef4ff;color:#274060;border-radius:14px;padding:10px 12px;font-size:12.5px;line-height:1.45}
.detailGeoNotice strong{color:#134e4a}.detailGeoNotice.warn{border-color:#f4d38d;background:#fff8e6;color:#7c4a03}.detailGeoNotice.bad{border-color:#ffccc7;background:#fff1f0;color:#8a1f11}
.detailSourceLine{margin-top:8px;color:var(--muted);font-size:12px;line-height:1.45}.detailSourceLine a{color:var(--brand);font-weight:850;text-decoration:none}
.detailThumbs{display:flex;gap:7px;overflow:auto;padding:8px 2px 0}.detailThumbs button{border:2px solid transparent;background:#fff;border-radius:10px;padding:0;min-width:58px;height:44px;overflow:hidden;cursor:pointer}.detailThumbs button[aria-current="true"]{border-color:var(--brand)}.detailThumbs img{width:100%;height:100%;object-fit:cover;display:block}.detailPhotoFallback{min-height:96px;display:flex;align-items:center;justify-content:center;color:var(--muted);background:#f1eee8;border:1px dashed var(--line);border-radius:14px;padding:12px;text-align:center}.detail-img img.is-broken{display:none}.card .photo .no-photo{height:100%;min-height:150px;display:flex;align-items:center;justify-content:center;color:var(--muted);background:#f1eee8}
@media(max-width:580px){.detailGeoNotice{font-size:12px}.detailThumbs button{min-width:54px;height:42px}.detailSourceLine{font-size:11.5px}}
'''

SCRIPT = r'''
<script id="wave2LotCDetailGeoPhoto">
(function(){
function escC(s){return (s??'').toString().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function attrC(s){return escC(s).replace(/`/g,'&#96;');}
function uniqueC(a){return [...new Set((a||[]).filter(Boolean).map(String))];}
function galleryC(x){const locals=uniqueC([...(x.local_image_urls||[]),...(x.local_image_url?[x.local_image_url]:[])]); const remotes=uniqueC([...(x.image_urls||[]),...(x.image_url?[x.image_url]:[])]); return locals.length?locals:remotes;}
function geoLevelC(x){return String(x.geo_quality?.level||x.location_intelligence?.quality||'source').toLowerCase();}
function isHighGeoC(x){const lvl=geoLevelC(x); const score=Number(x.geo_quality?.score||0); return ['haute','moyenne','quartier','commune'].includes(lvl) || score>=0.55;}
function geoNoticeC(x){
  const g=x.geo_quality||{}, mp=x.map_point||{}, li=x.location_intelligence||{};
  const lvl=geoLevelC(x), high=isHighGeoC(x);
  const label=mp.label||li.precise_location_label||x.location||x.city||'Réunion';
  const precision=mp.precision||g.note||'localisation issue de la source';
  const cls=high?'detailGeoNotice':'detailGeoNotice warn';
  const map=x.map_url||mp.osm_url||'';
  const mapLink=(high&&map)?` · <a href="${attrC(map)}" target="_blank" rel="noreferrer">ouvrir la carte approximative</a>`:'';
  return `<div class="${cls}" data-detail-geo-prudent><strong>Localisation prudente</strong> — ${escC(label)}. ${escC(precision)}. Adresse exacte non déduite ni inventée; à confirmer sur la source.${mapLink}</div>`;
}
function sourceLineC(x){const src=x.source||'source'; const sid=x.source_id||x.id||''; const seen=x.seen_last_at?new Date(x.seen_last_at).toLocaleString('fr-FR',{dateStyle:'short',timeStyle:'short'}):'date non précisée'; const url=x.url?` · <a href="${attrC(x.url)}" target="_blank" rel="noreferrer">annonce source</a>`:''; return `<div class="detailSourceLine" data-detail-source>Source: <strong>${escC(src)}</strong>${sid?' · réf. '+escC(sid):''} · vu: ${escC(seen)}${url}</div>`;}
let brokenModalC=new Set();
function renderModalPhotoC(idx){
  const root=document.querySelector('#mImg'); if(!root || !window.__wave2LotCGallery)return;
  const gallery=window.__wave2LotCGallery.filter(src=>!brokenModalC.has(src));
  if(!gallery.length){root.innerHTML='<div class="detailPhotoFallback">Photos source indisponibles ou cassées — vérifier l’annonce source.</div>'; return;}
  window.__wave2LotCIndex=((idx%gallery.length)+gallery.length)%gallery.length;
  const src=gallery[window.__wave2LotCIndex]; const multi=gallery.length>1;
  root.innerHTML=`<img src="${attrC(src)}" alt="Photo annonce"><button class="photoNav prevPhoto" aria-label="Photo précédente" ${multi?'':'hidden'} type="button">‹</button><button class="photoNav nextPhoto" aria-label="Photo suivante" ${multi?'':'hidden'} type="button">›</button><div class="photoCounter" ${multi?'':'hidden'}>${window.__wave2LotCIndex+1}/${gallery.length}</div>${multi?`<div class="detailThumbs" aria-label="Miniatures galerie">${gallery.slice(0,12).map((u,i)=>`<button type="button" data-wave2-photo="${i}" aria-current="${i===window.__wave2LotCIndex?'true':'false'}"><img src="${attrC(u)}" alt="Miniature ${i+1}"></button>`).join('')}</div>`:''}`;
  root.querySelector('img')?.addEventListener('error',()=>{brokenModalC.add(src); renderModalPhotoC(window.__wave2LotCIndex);},{once:true});
  root.querySelector('.prevPhoto')?.addEventListener('click',e=>{e.stopPropagation();renderModalPhotoC(window.__wave2LotCIndex-1);});
  root.querySelector('.nextPhoto')?.addEventListener('click',e=>{e.stopPropagation();renderModalPhotoC(window.__wave2LotCIndex+1);});
  root.querySelectorAll('[data-wave2-photo]').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation();renderModalPhotoC(Number(b.dataset.wave2Photo));}));
}
function hardenCardBrokenImagesC(){
  document.querySelectorAll('.card .photo img').forEach(img=>{ if(img.dataset.wave2Fallback)return; img.dataset.wave2Fallback='1'; img.addEventListener('error',()=>{const photo=img.closest('.photo'); if(photo){img.remove(); if(!photo.querySelector('.no-photo')) photo.insertAdjacentHTML('afterbegin','<div class="no-photo">Photo indisponible</div>');}}, {once:true}); });
}
const prevRenderC=typeof render==='function'?render:null; if(prevRenderC){render=function(res){prevRenderC(res); hardenCardBrokenImagesC();};}
const prevOpenC=typeof openDetail==='function'?openDetail:null; if(prevOpenC){openDetail=function(id){
  prevOpenC(id);
  const x=(window.all||all||[]).find(i=>String(i.id)===String(id)); if(!x)return;
  window.__wave2LotCGallery=galleryC(x); window.__wave2LotCIndex=0; brokenModalC=new Set();
  if(window.__wave2LotCGallery.length) renderModalPhotoC(0); else document.querySelector('#mImg').innerHTML='<div class="detailPhotoFallback">Aucune photo exploitable dans la source.</div>';
  const trust=document.querySelector('#mTrust'); if(trust){
    trust.querySelectorAll('[data-detail-geo-prudent],[data-detail-source]').forEach(el=>el.remove());
    trust.insertAdjacentHTML('afterbegin', sourceLineC(x)+geoNoticeC(x));
    // Remove embedded map for low-confidence records; keep a link only when the point is explicitly acceptable.
    if(!isHighGeoC(x)) trust.querySelector('.mapBox')?.remove();
  }
  const loc=document.querySelector('#mLoc'); if(loc){const txt=x.location||x.city||''; loc.textContent=txt ? txt+' · localisation approximative' : 'Localisation source non précisée';}
};}
setTimeout(()=>{try{hardenCardBrokenImagesC();}catch(e){console.error('wave2 lot C failed',e)}},1000);
window.__wave2LotCDetailGeoPhoto={galleryC,geoNoticeC,sourceLineC,renderModalPhotoC,isHighGeoC};
})();
</script>
'''

def _local_media_exists(app: Path, url: str) -> bool:
    if not url or url.startswith(('http://', 'https://', 'data:')):
        return True
    rel = url.lstrip('/')
    return (app / rel).exists()


def _geo_quality_for_item(item: dict) -> dict:
    loc = item.get('location_intelligence') or {}
    q = str(loc.get('quality') or '').lower()
    city = item.get('city') or loc.get('commune_inferred') or ''
    district = item.get('district') or loc.get('district_best') or ''
    if any(k in q for k in ['haute', 'high', 'quartier', 'adresse']):
        level, score = 'haute', 0.8
    elif city and district and str(city).strip().lower() != str(district).strip().lower():
        level, score = 'moyenne', 0.65
    elif city:
        level, score = 'commune', 0.45
    else:
        level, score = 'faible', 0.15
    return {'level': level, 'score': score, 'note': 'Localisation approximative sauf adresse explicitement fournie par la source.'}


def prune_missing_local_gallery_files(app: Path) -> dict:
    path = app / 'listings.json'
    if not path.exists():
        return {'checked': 0, 'removed': 0, 'listings_changed': 0}
    data = json.loads(path.read_text(encoding='utf-8'))
    items = data.get('listings') or []
    removed = 0
    changed = 0
    geo_enriched = 0
    for item in items:
        before_primary = item.get('local_image_url') or ''
        before_gallery = list(item.get('local_image_urls') or [])
        gallery = [u for u in before_gallery if _local_media_exists(app, str(u))]
        removed += max(0, len(before_gallery) - len(gallery))
        primary = before_primary if _local_media_exists(app, str(before_primary)) else ''
        if not primary and gallery:
            primary = gallery[0]
        if primary and primary not in gallery:
            gallery.insert(0, primary)
        if before_primary != primary or before_gallery != gallery:
            item['local_image_url'] = primary
            item['local_image_urls'] = gallery
            changed += 1
        if not isinstance(item.get('geo_quality'), dict):
            item['geo_quality'] = _geo_quality_for_item(item)
            geo_enriched += 1
            changed += 1
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    return {'checked': len(items), 'removed': removed, 'listings_changed': changed, 'geo_enriched': geo_enriched}


def patch_app(app: Path) -> dict:
    app = app.resolve()
    index = app / 'index.html'
    media_prune = prune_missing_local_gallery_files(app)
    if not index.exists():
        return {'ok': True, 'app': str(app), 'skipped_missing_files': ['index.html'], 'media_prune': media_prune}
    stamp = dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    backup = app.parent / f'{app.name}.bak.wave2-lot-c-detail-geo-photo-{stamp}'
    if not backup.exists():
        shutil.copytree(app, backup)
    html = index.read_text(encoding='utf-8')
    html = re.sub(r'\n<script id="wave2LotCDetailGeoPhoto">.*?</script>\n?', '\n', html, flags=re.S)
    html = html.replace(CSS, '')
    if '/* Wave 2 Lot C: detail-only gallery/fallback + prudent geo wording */' not in html:
        html = html.replace('</style>', CSS + '\n</style>', 1)
    html = html.replace('</body></html>', SCRIPT + '\n</body></html>')
    index.write_text(html, encoding='utf-8')
    return {'ok': True, 'index': str(index), 'backup': str(backup), 'media_prune': media_prune}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', default=str(APP), help='Static app directory to patch')
    args = parser.parse_args()
    print(patch_app(Path(args.app)))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
