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
    opp_by_id={x.get('id'):x for x in (opp.get('top') or []) if x.get('id')}
    for x in listings:
        x['seen_also_on']=[]
        x['dedup_product_note']=''
        # Display policy is non-destructive: every source row remains in
        # listings.json and dedup_groups.json, but strong auto-duplicates are
        # hidden from the default card grid so the portal does not show the
        # same home twice (e.g. OFIM + OFIM RSS, SeLoger + Zimo).
        x['display_canonical']=True
        x['canonical_display_id']=x.get('id')
        x['dedup_group_id']=''
        x['dedup_decision']=''
        top_item=opp_by_id.get(x.get('id')) or {}
        score=(x.get('opportunity_analysis') or {}).get('score') if isinstance(x.get('opportunity_analysis'), dict) else None
        if score is None:
            score=x.get('opportunity_score') or top_item.get('score')
        if isinstance(score, (int, float)):
            label=top_item.get('label') or ('opportunité forte' if score>=75 else 'à étudier' if score>=58 else 'standard' if score>=42 else 'risque/bruit')
            confidence=top_item.get('confidence') or (x.get('opportunity_analysis') or {}).get('confidence') or 'à vérifier'
            # Compact card annotation only; full explanations stay in opportunity.json
            # to avoid bloating/polluting the homepage payload.
            x['opportunity_score']=round(float(score))
            x['opportunity_analysis']={'score':round(float(score)),'score_version':opp.get('score_version'),'label':label,'confidence':confidence}
    for g in dedup.get('groups',[]):
        sources=sorted(set(g.get('sources') or []))
        member_ids=[mid for mid in (g.get('member_ids') or []) if mid in by_id]
        canonical_id=g.get('canonical_id') if g.get('canonical_id') in by_id else (member_ids[0] if member_ids else None)
        decision=g.get('decision') or ''
        for mid in member_ids:
            own=by_id[mid].get('source') or by_id[mid].get('source_site')
            other=[s for s in sources if s != own]
            by_id[mid]['seen_also_on']=other
            by_id[mid]['dedup_group_id']=g.get('group_id') or ''
            by_id[mid]['dedup_decision']=decision
            by_id[mid]['dedup_confidence']=g.get('confidence')
            by_id[mid]['dedup_sources']=sources
            by_id[mid]['dedup_role']='canonical' if mid == canonical_id else 'duplicate_or_variant'
            explanations=g.get('explanations') or []
            if explanations:
                by_id[mid]['dedup_reason']=str(explanations[0])[:240]
            by_id[mid]['canonical_display_id']=canonical_id or mid
            # Non-destructive display policy: only exact/strong auto_duplicate
            # non-canonicals are hidden from the default grid. needs_review stays
            # visible because it may represent two real flats in the same residence.
            if decision == 'auto_duplicate' and canonical_id and mid != canonical_id:
                by_id[mid]['display_canonical']=False
            if other:
                by_id[mid]['dedup_product_note']='Vu aussi sur '+', '.join(other[:4])
    out_payload={k:v for k,v in listings_payload.items() if k != 'listings'}
    out_payload.update({'generated_at':listings_payload.get('generated_at'),'count':len(listings),'listings':listings})
    lp.write_text(json.dumps(out_payload, ensure_ascii=False, separators=(',',':')), encoding='utf-8')
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
        'dedup':{'groups_count':dedup.get('groups_count'),'auto_duplicate':sum(1 for g in dedup.get('groups',[]) if g.get('decision')=='auto_duplicate'),'needs_review':sum(1 for g in dedup.get('groups',[]) if g.get('decision')=='needs_review'),'inter_source':sum(1 for g in dedup.get('groups',[]) if len(set(g.get('sources') or []))>1),'annotated_seen_also_on':sum(1 for x in listings if x.get('seen_also_on')),'hidden_from_default_grid':sum(1 for x in listings if x.get('display_canonical') is False)},
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
