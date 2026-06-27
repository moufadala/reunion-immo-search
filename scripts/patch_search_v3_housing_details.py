#!/usr/bin/env python3
import json, re, shutil, datetime
from pathlib import Path

APP = Path('/opt/data/projects/reunion-immo-search/artifacts/app')
INDEX = APP / 'index.html'
LISTINGS = APP / 'listings.json'
TS = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
BAK = APP.parent / f'app.bak.search-v3-housing-{TS}'

FR_NUM = {
    'un':1,'une':1,'deux':2,'trois':3,'quatre':4,'cinq':5,'six':6,'sept':7,'huit':8,'neuf':9,
    '1':1,'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,'9':9
}

def norm(s):
    import unicodedata
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii','ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+',' ',s.lower()).strip()

def count_phrase(text, nouns):
    """Return conservative count, confidence and evidence snippets for French room mentions."""
    t = str(text or '')
    hits = []
    total = 0
    for noun in nouns:
        # 2 salles de bain / deux salles d'eau / une salle d'eau / 1 douche
        pat = re.compile(rf"\b(?:(\d+|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf)\s+)?({noun})s?\b", re.I)
        for m in pat.finditer(t):
            nraw = (m.group(1) or '1').lower()
            n = FR_NUM.get(nraw, 1)
            total += n
            a,b = max(0,m.start()-45), min(len(t),m.end()+55)
            hits.append(re.sub(r'\s+',' ',t[a:b]).strip())
    if not hits:
        return None, 'absent', []
    # Do not overcount equivalent repeated marketing boilerplate too aggressively.
    return min(total, 9), 'source_text', hits[:4]

def count_bedrooms_in_segment(seg):
    s = seg.lower()
    total = 0
    for m in re.finditer(r"\b(?:(\d+|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf)\s+)?chambres?\b", s):
        nraw = (m.group(1) or '1').lower()
        total += FR_NUM.get(nraw, 1)
    return total or None

def extract_details(x):
    text = ' '.join(str(x.get(k) or '') for k in ['title','description'])
    tn = norm(text)
    bath_count, bath_conf, bath_ev = count_phrase(text, [r"salle\s+d[eu]\s+bain", r"salle\s+d[eu]\s+douche", r"salle\s+d['’]?eau", r"sdb", r"douche"])
    wc_count, wc_conf, wc_ev = count_phrase(text, [r"wc", r"toilettes?", r"sanitaires?"])
    duplex = bool(re.search(r'\bduplex\b', tn))
    rdc = bool(re.search(r'\b(rdc|rez de chaussee|plain pied|plein pied)\b', tn))
    upstairs = bool(re.search(r'\b(a l etage|à l étage|etage|étage|niveau superieur|duplex)\b', text, re.I)) or duplex
    single_storey = bool(re.search(r'\b(plain pied|plein pied|tout (?:en )?rdc|de plain pied)\b', tn))
    floor_mentions = []
    for pat in [r'(?:situe\w*\s+)?(?:au|en)\s+(\d+)(?:er|e|eme|ème)?\s+etage', r'(?:dernier\s+etage)', r'(?:rez[-\s]de[-\s]chaussee|rdc)', r'duplex', r'plain[-\s]pied']:
        for m in re.finditer(pat, text, re.I):
            floor_mentions.append(re.sub(r'\s+',' ',m.group(0)).strip())
    # bedroom distribution by crude sentence/section proximity
    lower = text.lower()
    ground_count = None; upstairs_count = None; dist_ev=[]
    # Find section after rdc/rez and after étage, cut before opposite marker.
    rdc_match = re.search(r'(?:rez[-\s]de[-\s]chaussée|rez[-\s]de[-\s]chaussee|rdc)[^.;:]{0,220}', text, re.I)
    if rdc_match:
        ground_count = count_bedrooms_in_segment(rdc_match.group(0)); dist_ev.append(re.sub(r'\s+',' ',rdc_match.group(0)).strip())
    # Common phrasing: "comprend ... une chambre ... et à l'étage ... trois chambres"
    before_up = re.search(r'comprend\s*:?(.{0,220}?)(?:a l[’\']?etage|à l[’\']?étage)', text, re.I|re.S)
    if before_up and ground_count is None:
        ground_count = count_bedrooms_in_segment(before_up.group(1)); dist_ev.append(re.sub(r'\s+',' ',before_up.group(0)).strip())
    up_match = re.search(r'(?:a l[’\']?etage|à l[’\']?étage|etage|étage)\s*:?[^.;]{0,240}', text, re.I)
    if up_match:
        upstairs_count = count_bedrooms_in_segment(up_match.group(0)); dist_ev.append(re.sub(r'\s+',' ',up_match.group(0)).strip())
    layout = 'Non précisé'
    if single_storey:
        layout = 'Tout RDC / plain-pied'
    elif duplex or (rdc and upstairs):
        layout = 'Duplex / étage(s)'
    elif rdc:
        layout = 'RDC mentionné'
    elif upstairs:
        layout = 'Étage mentionné'
    return {
        'version':'housing_details_v1',
        'bathrooms': {'count': bath_count, 'confidence': bath_conf, 'evidence': bath_ev},
        'wc': {'count': wc_count, 'confidence': wc_conf, 'evidence': wc_ev},
        'layout': {'label': layout, 'duplex': duplex, 'rdc_mentioned': rdc, 'upstairs_mentioned': upstairs, 'single_storey': single_storey, 'evidence': floor_mentions[:5]},
        'bedroom_distribution': {'ground_floor_count': ground_count, 'upstairs_count': upstairs_count, 'evidence': dist_ev[:4]},
    }

def fix_furnished_tags(x):
    da = x.get('description_analysis') or {}
    furnished_status = (((da.get('property_state') or {}).get('furnished') or {}).get('status'))
    tags = list(x.get('feature_tags') or [])
    if furnished_status == 'non_meuble':
        x['furnished'] = 'non_meuble'
        x['feature_tags'] = [t for t in tags if norm(t) not in {'meuble','meublee'}]
    elif furnished_status == 'meuble':
        x['furnished'] = 'meuble'
        if not any(norm(t) == 'meuble' for t in tags):
            x['feature_tags'] = tags + ['Meublé']

SEARCH_V3 = r'''

<script id="searchUXV3HousingDetails">
(function(){
function n3(s){return String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim().replace(/\s+/g,' ');}
function e3(s){return (s??'').toString().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function a3(s){return e3(s).replace(/`/g,'&#96;');}
const LOCS=[
 {label:'Rivière des Pluies', commune:'Sainte-Marie', aliases:['riviere des pluies','rivieres des pluies','rivière des pluies','rivières des pluies']},
 {label:'Beauséjour', commune:'Sainte-Marie', aliases:['beausejour','beauséjour']},
 {label:'Grande Montée', commune:'Sainte-Marie', aliases:['grande montee','grande montée','la grande montee','la grande montée']},
 {label:'Duparc', commune:'Sainte-Marie', aliases:['duparc']},
 {label:'Bretagne', commune:'Saint-Denis', aliases:['bretagne','la bretagne']},
 {label:'Sainte-Marie', commune:'Sainte-Marie', aliases:['sainte marie','ste marie','ste-marie','la convenance']},
 {label:'Saint-Denis', commune:'Saint-Denis', aliases:['saint denis','st denis','st-denis','sainte clotilde','ste clotilde','moufia','bellepierre','chaudron']},
 {label:'Sainte-Suzanne', commune:'Sainte-Suzanne', aliases:['sainte suzanne','ste suzanne','quartier francais','quartier français','bagatelle']},
 {label:'Saint-André', commune:'Saint-André', aliases:['saint andre','saint andré','st andre','cambuston','champ borne']}
].map(l=>({...l, aliases:[l.label,...l.aliases].map(n3)})).sort((a,b)=>Math.max(...b.aliases.map(x=>x.length))-Math.max(...a.aliases.map(x=>x.length)));
const AMENITIES={Parking:['parking','garage','stationnement','place de parking'], 'Varangue / terrasse':['varangue','terrasse','balcon'], Jardin:['jardin'], Climatisation:['clim','climatisation'], Ascenseur:['ascenseur']};
function chip(type,label,value,soft=false){return {type,label,value:value??label,soft};}
function parseV3(q){let raw=n3(q).replace(/\bst\b/g,'saint').replace(/\bste\b/g,'sainte'); let work=' '+raw+' '; const chips=[], rooms=[], amenities=[], excludes=[], locations=[]; let typeHint='', minSurface=null, minPrice=null, maxPrice=null, priceOp='', barePrice=null;
 [...work.matchAll(/\b[tf]\s*([1-9])\b/g)].forEach(m=>{const v=Number(m[1]); if(!rooms.includes(v)){rooms.push(v);chips.push(chip('rooms','T/F'+v,v));} work=work.replace(m[0],' ');});
 if(/\bstudio\b/.test(work)){rooms.push(1); chips.push(chip('rooms','Studio / T1',1)); work=work.replace(/\bstudio\b/g,' ');}
 const between=work.match(/(?:entre|de)\s*(\d{3,5})\s*(?:et|a|à|-)\s*(\d{3,5})/); if(between){minPrice=Number(between[1]);maxPrice=Number(between[2]);priceOp='range';chips.push(chip('budget','Entre '+minPrice+' et '+maxPrice+' €',{min:minPrice,max:maxPrice}));work=work.replace(between[0],' ');}
 const pmin=work.match(/(?:plus de|plus|au dessus de|min|minimum|>=|>)\s*(\d{2,5})/); if(pmin&&!priceOp){minPrice=Number(pmin[1]);priceOp='min';chips.push(chip('budget','≥ '+minPrice+' €',minPrice));work=work.replace(pmin[0],' ');}
 const pmax=work.match(/(?:moins de|moins|sous|max|budget max|loyer max|jusqu a|jusqua|<=|<)\s*(\d{2,5})/); if(pmax&&!priceOp){maxPrice=Number(pmax[1]);priceOp='max';chips.push(chip('budget','≤ '+maxPrice+' €',maxPrice));work=work.replace(pmax[0],' ');}
 const peq=work.match(/(?:=|exactement|egal|égal)\s*(\d{3,5})|(\d{3,5})\s*(?:exactement|egal|égal)/); if(peq&&!priceOp){barePrice=Number(peq[1]||peq[2]);priceOp='equal';chips.push(chip('budget','= '+barePrice+' €',barePrice));work=work.replace(peq[0],' ');}
 const paround=work.match(/(?:autour de|environ|vers|aux alentours de)\s*(\d{3,5})|(\d{3,5})\s*(?:environ|autour)/); if(paround&&!priceOp){barePrice=Number(paround[1]||paround[2]);priceOp='around';chips.push(chip('budget','Autour de '+barePrice+' €',barePrice));work=work.replace(paround[0],' ');}
 if(!priceOp){const nums=[...work.matchAll(/\b(\d{3,5})\s*(?:€|eur|euros)?\b/g)].map(m=>Number(m[1])).filter(v=>v>=300&&v<=10000); if(nums.length){barePrice=Math.max(...nums); chips.push(chip('budget','Budget '+barePrice+' € à préciser',barePrice,true));}}
 const surf=work.match(/(\d{1,3})\s*(?:m2|m²|metres|metre|m)\b/); if(surf){minSurface=Number(surf[1]); chips.push(chip('surface','≥ '+minSurface+' m²',minSurface)); work=work.replace(surf[0],' ');}
 if(/\b(maison|villa)\b/.test(work)){typeHint='maison';chips.push(chip('type','Maison / villa','maison'));work=work.replace(/\b(maison|villa)\b/g,' ');} if(/\b(appartement|appart|apt)\b/.test(work)){typeHint='appartement';chips.push(chip('type','Appartement','appartement'));work=work.replace(/\b(appartement|appart|apt)\b/g,' ');}
 for(const l of LOCS){const hit=l.aliases.find(al=>al.length>2 && work.includes(' '+al+' ')); if(hit){locations.push(l);chips.push(chip('location',l.label,l.label)); for(const al of l.aliases){work=work.replaceAll(' '+al+' ',' ');}}}
 if(/\b(non|pas|sans)\s+meuble\b|\blocation nue\b|\bloue vide\b/.test(work)){excludes.push('Meublé');chips.push(chip('exclude','Non meublé','Meublé'));work=work.replace(/\b(non|pas|sans)\s+meuble\b|\blocation nue\b|\bloue vide\b/g,' ');} 
 for(const [label,aliases] of Object.entries(AMENITIES)){for(const al of aliases.map(n3)){if(work.includes(' '+al+' ')){amenities.push(label);chips.push(chip('amenity',label,label));work=work.replaceAll(' '+al+' ',' ');break;}}}
 const stop=new Set('le la les l un une des du de d a au aux en sur dans avec sans et ou pour moins sous max budget loyer jusqu jusqua entre proche pres près vers autour m2 m metres metre min minimum plus piece pieces p ch chambre chambres euro euros eur meuble non pas'.split(' '));
 const tokens=[...new Set(work.split(/\s+/).filter(Boolean).filter(x=>!stop.has(x)&&!/^[tf][1-9]$/.test(x)&&!/^\d{1,5}$/.test(x)))]; return {raw,tokens,rooms,maxPrice,minPrice,barePrice,priceOp,minSurface,typeHint,amenities:[...new Set(amenities)],excludes:[...new Set(excludes)],locations,chips};}
function hay3(x){return x._hay||n3([x.id,x.source_id,x.title,x.city,x.district,x.location,x.region,x.type,x.source,x.agency,x.url,x.description,x.rooms?('t'+x.rooms+' f'+x.rooms+' '+x.rooms+' pieces'):'',x.bedrooms?x.bedrooms+' chambres':'',x.furnished||'',(x.feature_tags||[]).join(' '),x.location_intelligence?.district_best||'',x.location_intelligence?.precise_location_label||'',x.location_intelligence?.commune_inferred||''].join(' '));}
function matchesLoc(x,l,relaxed=false){if(relaxed){return n3(x.city)===n3(l.commune)||n3(x.location_intelligence?.commune_inferred)===n3(l.commune);} const h=hay3(x); return l.aliases.some(a=>a && h.includes(a)) || (n3(l.label)===n3(l.commune) && n3(x.city)===n3(l.commune));}
function explicitMeuble(x){const st=x.description_analysis?.property_state?.furnished?.status; if(st==='non_meuble')return false; if(st==='meuble')return true; const f=n3(x.furnished||''); const tags=(x.feature_tags||[]).map(n3); return ['meuble','meublee'].includes(f)||tags.includes('meuble')||tags.includes('meublee');}
function score3(x,p){let s=0,h=hay3(x); for(const t of p.tokens){s+=h.split(' ').includes(t)?8:(h.includes(t)?3:0);} for(const l of p.locations){if(matchesLoc(x,l,false))s+=50; else if(matchesLoc(x,l,true))s+=18;} for(const a of p.amenities){if((x.feature_tags||[]).includes(a)||h.includes(n3(a)))s+=10;} return s;}
function filter3(p,relaxed=false){let res=all.filter(x=>!hidden.has(x.id)); if(p.locations.length)res=res.filter(x=>p.locations.some(l=>matchesLoc(x,l,relaxed))); if(p.tokens.length)res=res.filter(x=>p.tokens.every(t=>hay3(x).includes(t))); if(p.rooms.length)res=res.filter(x=>p.rooms.includes(Number(x.rooms))); if(p.typeHint&&!state.type)res=res.filter(x=>n3(x.type).includes(p.typeHint)); if(p.amenities.length)res=res.filter(x=>p.amenities.every(a=>(x.feature_tags||[]).includes(a)||hay3(x).includes(n3(a)))); if(p.excludes.includes('Meublé'))res=res.filter(x=>!explicitMeuble(x)); if(state.city)res=res.filter(x=>x.city===state.city); if(state.type)res=res.filter(x=>x.type===state.type); if(state.region)res=res.filter(x=>x.region===state.region); if(state.maxPrice)res=res.filter(x=>x.price&&x.price<=Number(state.maxPrice)); if(state.minPrice)res=res.filter(x=>x.price&&x.price>=Number(state.minPrice)); if(p.maxPrice)res=res.filter(x=>x.price&&x.price<=p.maxPrice); if(p.minPrice)res=res.filter(x=>x.price&&x.price>=p.minPrice); if(p.priceOp==='equal'&&p.barePrice)res=res.filter(x=>x.price&&Math.abs(Number(x.price)-p.barePrice)<=25); if(p.priceOp==='around'&&p.barePrice)res=res.filter(x=>x.price&&Math.abs(Number(x.price)-p.barePrice)<=100); const ms=state.minSurface||p.minSurface; if(ms)res=res.filter(x=>x.surface&&x.surface>=Number(ms)); if(state.minRooms)res=res.filter(x=>x.rooms&&Number(x.rooms)>=Number(state.minRooms)); return res;}
function priceOps(p){if(!p.barePrice||p.priceOp)return ''; const v=p.barePrice; return `<span class="nlchip soft priceChoice"><span>${v} € ?</span><button data-v3-price="max" data-v="${v}">≤</button><button data-v3-price="min" data-v="${v}">≥</button><button data-v3-price="equal" data-v="${v}">=</button><button data-v3-price="around" data-v="${v}">autour</button></span>`;}
function renderChips3(p,msg){const root=document.querySelector('#nlChips'); if(root)root.innerHTML=(p.chips||[]).map((c,i)=>`<span class="nlchip ${c.soft?'soft':''}">${e3(c.label)}<button type="button" data-v3-remove="${i}" title="Retirer">×</button></span>`).join('')+priceOps(p); const u=document.querySelector('#understood'); if(u)u.textContent=msg||((p.chips||[]).length?'Critères actifs ci-dessus.':'Tape une recherche naturelle : F4 non meublé Beauséjour 900, moins 900, autour de 1000…'); const sug=document.querySelector('#suggestions'); if(sug)sug.innerHTML='';}
function reasons3(x,p){const out=[]; if(p.rooms.includes(Number(x.rooms)))out.push('T/F'+x.rooms); for(const l of p.locations){if(matchesLoc(x,l,false))out.push(l.label); else if(matchesLoc(x,l,true))out.push(l.commune+' élargi');} if(p.excludes.includes('Meublé')&&!explicitMeuble(x))out.push('non meublé'); if(p.maxPrice&&x.price<=p.maxPrice)out.push('≤ budget'); if(p.minPrice&&x.price>=p.minPrice)out.push('≥ budget'); return out.slice(0,5);}
function addBadges3(p){document.querySelectorAll('.card').forEach(c=>{c.querySelector('.matchReasons')?.remove(); c.querySelector('.geoHint')?.remove(); const x=all.find(i=>String(i.id)===String(c.dataset.id)); const body=c.querySelector('.body'), actions=c.querySelector('.actions'); if(!x||!body||!actions)return; const rs=reasons3(x,p); if(rs.length){const div=document.createElement('div');div.className='matchReasons';div.innerHTML=rs.map(r=>`<span class="reason">${e3(r)}</span>`).join('');body.insertBefore(div,actions);} });}
function apply3(updateUrl=true){const p=parseV3(state.q); let res=filter3(p,false), relaxed=false, msg=''; if(res.length===0&&p.locations.some(l=>l.commune&&n3(l.label)!==n3(l.commune))){const r=filter3(p,true); if(r.length){res=r;relaxed=true;msg='Aucun exact quartier : affichage élargi à la commune source.';}}
 const s=state.sort; res.sort((a,b)=>s==='price_asc'?(a.price||9e9)-(b.price||9e9):s==='price_desc'?(b.price||0)-(a.price||0):s==='surface_desc'?(b.surface||0)-(a.surface||0):s==='recent'?String(b.seen_last_at||'').localeCompare(String(a.seen_last_at||'')):(score3(b,p)*100+(b.opportunity_score||b.score||0))-(score3(a,p)*100+(a.opportunity_score||a.score||0))); render(res); renderChips3(p,msg); addBadges3(p); if(updateUrl)syncUrl(); window.__searchV3Last={parsed:p,count:res.length,relaxed}; return res;}
function replacePrice(q,v,op){const word=op==='max'?'moins ':op==='min'?'plus ':op==='equal'?'= ':'autour de '; const repl=word+v; let s=String(q||''); const phrase=/(moins de|moins|sous|max|budget max|loyer max|jusqu a|jusqua|plus de|plus|au dessus de|min|minimum|autour de|environ|vers|=|exactement|egal|égal)\s*\d{3,5}/i; if(phrase.test(s))return s.replace(phrase,repl); const bare=new RegExp('(^|\\s)'+v+'(?=\\s|€|eur|euros|$)','i'); if(bare.test(s))return s.replace(bare,(m,p)=>p+repl); return (s.trim()+' '+repl).trim();}
function stripByType(q,c){let s=' '+String(q||' ')+' '; if(c.type==='budget')s=s.replace(/\b(entre|de)\s*\d{3,5}\s*(et|a|à|-)\s*\d{3,5}\b/ig,' ').replace(/\b(moins de|moins|sous|max|budget max|loyer max|jusqu a|jusqua|plus de|plus|au dessus de|min|minimum|autour de|environ|vers|=|exactement|egal|égal)\s*\d{3,5}\b/ig,' ').replace(/\b\d{3,5}\s*(€|eur|euros)?\b/ig,' ');
 else if(c.type==='rooms')s=s.replace(/\b[tf]\s*[1-9]\b/ig,' ').replace(/\bstudio\b/ig,' '); else if(c.type==='exclude')s=s.replace(/\b(non|pas|sans)\s+meubl[ée]e?\b|\blocation nue\b|\bloue vide\b/ig,' '); else if(c.type==='location'){const l=LOCS.find(x=>x.label===c.value); if(l)for(const al of [l.label,...l.aliases])s=s.replace(new RegExp('\\b'+al.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b','ig'),' ');} else if(c.type==='type')s=s.replace(/\b(maison|villa|appartement|appart|apt)\b/ig,' '); else if(c.type==='amenity')s=s.replace(new RegExp('\\b'+String(c.value).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b','ig'),' '); return s.replace(/\s+/g,' ').trim();}
function hPill(x){const d=x.housing_details||{}; const out=[]; if(d.bathrooms?.count)out.push(`${d.bathrooms.count} sdb/eau`); if(d.wc?.count)out.push(`${d.wc.count} WC`); if(d.layout?.label&&d.layout.label!=='Non précisé')out.push(d.layout.label); const bd=d.bedroom_distribution||{}; if(bd.ground_floor_count)out.push(`${bd.ground_floor_count} ch. bas`); if(bd.upstairs_count)out.push(`${bd.upstairs_count} ch. haut`); return out.slice(0,4).map(t=>`<span class="pill housingPill">${e3(t)}</span>`).join('');}
const prevCard=card; card=function(x){let html=prevCard(x); const pills=hPill(x); return pills?html.replace('<div class="actions">',pills+'<div class="actions">'):html;};
const prevOpen=openDetail; openDetail=function(id){prevOpen(id); const x=all.find(i=>String(i.id)===String(id)); if(!x)return; const d=x.housing_details||{}, trust=document.querySelector('#mTrust'); if(!trust||trust.querySelector('.housingDetailBox'))return; const bd=d.bedroom_distribution||{}; const ev=[...(d.bathrooms?.evidence||[]),...(d.wc?.evidence||[]),...(d.layout?.evidence||[]),...(bd.evidence||[])].slice(0,5); const html=`<div class="evidenceBox housingDetailBox"><h4>Pièces d’eau / niveaux</h4><ul><li>Sdb/eau: ${e3(d.bathrooms?.count??'non précisé')}</li><li>WC: ${e3(d.wc?.count??'non précisé')}</li><li>Niveaux: ${e3(d.layout?.label||'non précisé')}</li><li>Chambres: ${bd.ground_floor_count?e3(bd.ground_floor_count)+' en bas · ':''}${bd.upstairs_count?e3(bd.upstairs_count)+' en haut':'non précisé'}</li></ul>${ev.length?'<p>Indices source: '+e3(ev.join(' · '))+'</p>':'<p>Non trouvé explicitement dans la description source.</p>'}</div>`; trust.insertAdjacentHTML('afterbegin',html);};
apply=apply3; parseQuery=parseV3; window.__searchUXV3={parseV3,apply3,replacePrice};
document.querySelector('#nlChips')?.addEventListener('click',function(ev){const po=ev.target.closest('[data-v3-price]'); const rm=ev.target.closest('[data-v3-remove]'); if(!po&&!rm)return; ev.preventDefault(); ev.stopImmediatePropagation(); const p=parseV3(state.q); if(po){state.q=replacePrice(state.q,po.dataset.v,po.dataset.v3Price);} if(rm){const c=p.chips[Number(rm.dataset.v3Remove)]; state.q=stripByType(state.q,c);} syncInputs(); apply3();}, true);
setTimeout(()=>{try{all.forEach(x=>{x._hay=hay3(x)}); apply3(false);}catch(err){console.error('search UX v3 failed',err)}},800);
})();
</script>
'''

def main():
    if not BAK.exists():
        shutil.copytree(APP, BAK)
    data = json.loads(LISTINGS.read_text())
    changed = 0
    for x in data['listings']:
        before = json.dumps(x.get('housing_details'), sort_keys=True, ensure_ascii=False)
        fix_furnished_tags(x)
        x['housing_details'] = extract_details(x)
        after = json.dumps(x.get('housing_details'), sort_keys=True, ensure_ascii=False)
        if before != after:
            changed += 1
    data['semantic_enrichment'] = {'version':'housing_details_v1','generated_at':datetime.datetime.utcnow().isoformat()+ 'Z','changed_listings':changed}
    LISTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    html = INDEX.read_text()
    # Remove old v3 script if rerun
    html = re.sub(r'\n<script id="searchUXV3HousingDetails">.*?</script>\n', '\n', html, flags=re.S)
    html = html.replace('</body></html>', SEARCH_V3 + '\n</body></html>')
    # CSS small tweak: less visual duplication / compact housing pills
    css = '.housingPill{background:#eef4ff;color:#274060}.priceChoice button{border:0;background:transparent;color:#7c4a03;font-weight:950;padding:0 3px}.searchFeedback .understood{min-height:18px}\n'
    if '.housingPill{' not in html:
        html = html.replace('</style>', css + '</style>')
    INDEX.write_text(html)
    print(json.dumps({'ok':True,'backup':str(BAK),'changed_listings':changed,'index':str(INDEX),'listings':str(LISTINGS)}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
