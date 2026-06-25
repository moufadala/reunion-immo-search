#!/usr/bin/env python3
from __future__ import annotations
import argparse, collections, html, json, statistics
from pathlib import Path


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--app', default='artifacts/app')
    args=ap.parse_args()
    app=Path(args.app).resolve()
    lp=app/'listings.json'; op=app/'opportunity.json'; dp=app/'dedup_groups.json'
    listings_payload=json.loads(lp.read_text(encoding='utf-8'))
    listings=listings_payload.get('listings') or []
    opp=json.loads(op.read_text(encoding='utf-8'))
    dedup=json.loads(dp.read_text(encoding='utf-8'))
    by_id={x.get('id'):x for x in listings if x.get('id')}
    for x in listings:
        x['seen_also_on']=[]
        x['dedup_product_note']=''
    for g in dedup.get('groups',[]):
        sources=sorted(set(g.get('sources') or []))
        for mid in g.get('member_ids') or []:
            if mid in by_id:
                own=by_id[mid].get('source') or by_id[mid].get('source_site')
                other=[s for s in sources if s != own]
                by_id[mid]['seen_also_on']=other
                if other:
                    by_id[mid]['dedup_product_note']='Vu aussi sur '+', '.join(other[:4])
    lp.write_text(json.dumps({'generated_at':listings_payload.get('generated_at'),'count':len(listings),'listings':listings}, ensure_ascii=False, separators=(',',':')), encoding='utf-8')
    scores=[(x.get('opportunity_analysis') or {}).get('score') or x.get('opportunity_score') or 0 for x in listings]
    comm=collections.defaultdict(list)
    for x in listings:
        if x.get('city') and x.get('price') and x.get('surface'):
            comm[x['city']].append(round(float(x['price'])/max(float(x['surface']),1),2))
    comm_summary={k:{'n':len(v),'median_price_m2':round(statistics.median(v),2)} for k,v in comm.items() if len(v)>=3}
    report={
        'version':'opportunity_dedup_calibration_v1',
        'score_version':opp.get('score_version'),
        'listings':len(listings),
        'score_buckets':{'strong_75_plus':sum(1 for s in scores if s>=75),'medium_55_74':sum(1 for s in scores if 55<=s<75),'low_under_55':sum(1 for s in scores if s<55)},
        'top_count':len(opp.get('top') or []),
        'top_examples':[{k:item.get(k) for k in ['id','title','source','score','label','confidence','reasons']} for item in (opp.get('top') or [])[:10]],
        'dedup':{'groups_count':dedup.get('groups_count'),'auto_duplicate':sum(1 for g in dedup.get('groups',[]) if g.get('decision')=='auto_duplicate'),'needs_review':sum(1 for g in dedup.get('groups',[]) if g.get('decision')=='needs_review'),'inter_source':sum(1 for g in dedup.get('groups',[]) if len(set(g.get('sources') or []))>1),'annotated_seen_also_on':sum(1 for x in listings if x.get('seen_also_on'))},
        'commune_price_m2':dict(sorted(comm_summary.items(), key=lambda kv: kv[0])),
        'contract':['opportunité forte = score >=75 avec raisons et confiance','dédup non destructive: aucune suppression automatique','vu aussi sur = signal produit, pas fusion cachée','score calibré par prix/m² communal quand échantillon suffisant'],
    }
    (app/'opportunity_calibration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    block='<section class="panel"><h2>Calibration opportunités & doublons</h2><p>Score expliqué, déduplication non destructive, et signal “vu aussi sur”.</p><ul>'+''.join(f'<li><b>{html.escape(k)}</b>: {html.escape(str(v))}</li>' for k,v in report['score_buckets'].items())+'</ul><p>Doublons: '+html.escape(json.dumps(report['dedup'], ensure_ascii=False))+'</p></section>'
    for name in ['opportunites.html','opportunity.html']:
        hp=app/name
        if hp.exists():
            text=hp.read_text(encoding='utf-8', errors='replace')
            if 'Calibration opportunités & doublons' not in text:
                text=text.replace('</main>', block+'</main>') if '</main>' in text else text+block
                hp.write_text(text, encoding='utf-8')
    print(json.dumps({'ok':True,'app':str(app),'dedup':report['dedup'],'score_buckets':report['score_buckets']}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
