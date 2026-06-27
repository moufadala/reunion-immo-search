#!/usr/bin/env python3
import json, re, datetime, shutil
from pathlib import Path
P=Path('/opt/data/projects/reunion-immo-search/artifacts/app/listings.json')
TS=datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
BAK=P.with_suffix(f'.before-bedroomdist-v2-{TS}.json')
shutil.copy2(P, BAK)
FR={'un':1,'une':1,'deux':2,'trois':3,'quatre':4,'cinq':5,'six':6,'sept':7,'huit':8,'neuf':9}

def count_ch(seg):
    total=0
    for m in re.finditer(r"\b(?:(\d+|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf)\s+)?chambres?\b", seg or '', re.I):
        raw=(m.group(1) or '1').lower(); total+=int(raw) if raw.isdigit() else FR.get(raw,1)
    return total or None

def clean(s): return re.sub(r'\s+',' ',s or '').strip()

def dist(text, total_bedrooms=None):
    text=text or ''
    low=None; up=None; ev=[]
    # Strong pattern: RDC part before explicit étage marker, then étage part after.
    m=re.search(r'(rez[-\s]de[-\s]chaussée|rez[-\s]de[-\s]chaussee|rdc)(.*?)(?:à\s+l[’\']?étage|a\s+l[’\']?etage|l[’\']?étage|l etage)(.*?)(?:\.|$)', text, re.I|re.S)
    if m:
        low=count_ch(m.group(2)); up=count_ch(m.group(3)); ev.append(clean(m.group(0))[:280])
    else:
        m2=re.search(r'comprend\s*:?(.*?)(?:à\s+l[’\']?étage|a\s+l[’\']?etage|l[’\']?étage|l etage)(.*?)(?:\.|$)', text, re.I|re.S)
        if m2:
            low=count_ch(m2.group(1)); up=count_ch(m2.group(2)); ev.append(clean(m2.group(0))[:280])
        else:
            upm=re.search(r'(?:à\s+l[’\']?étage|a\s+l[’\']?etage|l[’\']?étage|l etage)\s*:?(.*?)(?:\.|$)', text, re.I|re.S)
            if upm:
                up=count_ch(upm.group(1)); ev.append(clean(upm.group(0))[:220])
    # Sanity: never publish impossible split.
    if total_bedrooms and low and up and low+up>int(total_bedrooms):
        # If exact total appears in one side + other side, prefer explicit split only when sum == total.
        return {'ground_floor_count': None, 'upstairs_count': None, 'evidence': ev[:2], 'confidence':'conflict_hidden'}
    return {'ground_floor_count': low, 'upstairs_count': up, 'evidence': ev[:2], 'confidence':'source_text' if (low or up) else 'absent'}

data=json.load(open(P))
changed=0
for x in data['listings']:
    d=x.get('housing_details') or {}
    old=d.get('bedroom_distribution')
    new=dist(x.get('description') or '', x.get('bedrooms'))
    d['bedroom_distribution']=new
    x['housing_details']=d
    if old!=new: changed+=1
data['semantic_enrichment']['bedroom_distribution_version']='v2_sane_split'
data['semantic_enrichment']['bedroom_distribution_updated_at']=datetime.datetime.utcnow().isoformat()+'Z'
P.write_text(json.dumps(data,ensure_ascii=False,indent=2))
print({'ok':True,'backup':str(BAK),'changed':changed})
