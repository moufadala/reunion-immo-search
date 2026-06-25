#!/usr/bin/env python3
"""Generate non-destructive intelligence layers for the clean Réunion immo portal.

Outputs separate pages/data files; annotates the current app/listings.json with
small derived fields only. It never deletes or merges source listings.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = Path(os.environ.get("IMMO_APP_PATH", str(ROOT / "artifacts" / "app")))
LISTINGS = APP / "listings.json"

COMMUNES: dict[str, dict[str, Any]] = {
    "Saint-Denis": {"region": "Nord", "interco": "CINOR", "aliases": ["st denis", "saint denis", "denis", "moufia", "bellepierre", "la bretagne", "bois de nefles sainte clotilde", "sainte clotilde", "sainte coltilde", "montgaillard", "bas de la riviere", "la montagne", "chaudron", "le chaudron", "champ fleuri", "camelia", "les camelias", "brule", "le brule", "providence", "domenjod", "primat", "foucherolles", "finette"]},
    "Sainte-Marie": {"region": "Nord", "interco": "CINOR", "aliases": ["ste marie", "sainte marie", "duparc", "la convenance", "beauséjour", "beausejour", "la grande montee", "grande montee", "rivière des pluies", "riviere des pluies", "ravine des chevres", "la ressource", "gillot", "bois rouge", "les cafes", "grand prado"]},
    "Sainte-Suzanne": {"region": "Nord", "interco": "CINOR", "aliases": ["ste suzanne", "sainte suzanne", "quartier francais", "bagatelle", "deux rives", "bocage"]},
    "Saint-André": {"region": "Est", "interco": "CIREST", "aliases": ["st andre", "saint andre", "cambuston", "champ borne", "centre ville saint andre", "ravine creuse"]},
    "Bras-Panon": {"region": "Est", "interco": "CIREST", "aliases": ["bras panon", "rivière du mat", "riviere du mat"]},
    "Saint-Benoît": {"region": "Est", "interco": "CIREST", "aliases": ["st benoit", "saint benoit", "bras fusil", "beaulieu", "sainte anne"]},
    "Sainte-Rose": {"region": "Est", "interco": "CIREST", "aliases": ["ste rose", "sainte rose"]},
    "Salazie": {"region": "Est", "interco": "CIREST", "aliases": ["salazie", "hell bourg", "hell-bourg"]},
    "Saint-Paul": {"region": "Ouest", "interco": "TCO", "aliases": ["st paul", "saint paul", "plateau caillou", "boucan canot", "saint gilles", "st gilles", "la saline", "l'ermitage", "hermitage", "bois de nefles saint paul"]},
    "Le Port": {"region": "Ouest", "interco": "TCO", "aliases": ["le port", "port"]},
    "La Possession": {"region": "Ouest", "interco": "TCO", "aliases": ["la possession", "possession", "ravine a malheur", "dos d'ane"]},
    "Trois-Bassins": {"region": "Ouest", "interco": "TCO", "aliases": ["trois bassins", "3 bassins"]},
    "Saint-Leu": {"region": "Ouest", "interco": "TCO", "aliases": ["st leu", "saint leu", "piton saint leu", "la fontaine", "etang saint leu"]},
    "Saint-Pierre": {"region": "Sud", "interco": "CIVIS", "aliases": ["st pierre", "saint pierre", "terre sainte", "ravine blanche", "bois d'olives", "ligne paradis", "grands bois"]},
    "Le Tampon": {"region": "Sud", "interco": "CASUD", "aliases": ["le tampon", "tampon", "trois mares", "la plaine des cafres", "bourg murat", "bras creux", "la chatoire"]},
    "Saint-Louis": {"region": "Sud", "interco": "CIVIS", "aliases": ["st louis", "saint louis", "la riviere saint louis", "riviere saint louis", "makes"]},
    "Étang-Salé": {"region": "Sud", "interco": "CIVIS", "aliases": ["etang sale", "étang salé", "l'etang sale", "etang-salé", "etang-sale"]},
    "Les Avirons": {"region": "Sud", "interco": "CIVIS", "aliases": ["les avirons", "avirons"]},
    "Entre-Deux": {"region": "Sud", "interco": "CASUD", "aliases": ["entre deux", "entre-deux"]},
    "Petite-Île": {"region": "Sud", "interco": "CASUD", "aliases": ["petite ile", "petite-ile", "petite île"]},
    "Saint-Joseph": {"region": "Sud", "interco": "CASUD", "aliases": ["st joseph", "saint joseph", "manapany", "langevin", "vincendo"]},
    "Saint-Philippe": {"region": "Sud", "interco": "CASUD", "aliases": ["st philippe", "saint philippe"]},
    "Cilaos": {"region": "Sud", "interco": "CIVIS", "aliases": ["cilaos"]},
    "Plaine-des-Palmistes": {"region": "Est", "interco": "CIREST", "aliases": ["plaine des palmistes", "la plaine des palmistes"]},
}

NORTH_EAST_PRIORITY = {"Saint-Denis", "Sainte-Marie", "Sainte-Suzanne", "Saint-André", "Bras-Panon", "Saint-Benoît"}


def norm(s: Any) -> str:
    s = str(s or "").lower().replace("-", " ").replace("_", " ").replace("'", " ")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()

# Centroïdes publics approximatifs de quartiers/lieux-dits utiles pour la recherche
# immobilière. Ils servent à placer une annonce sur une carte quand l'adresse exacte
# n'est pas publiée. Source principale: OpenStreetMap/Nominatim + PLU/communes; ne
# jamais présenter ces coordonnées comme une adresse précise.
DISTRICTS: dict[str, dict[str, Any]] = {
    "La Bretagne": {"commune": "Saint-Denis", "lat": -20.9239013, "lon": 55.5049113, "aliases": ["la bretagne", "bretagne"]},
    "Rivière des Pluies": {"commune": "Sainte-Marie", "lat": -20.9105920, "lon": 55.5096169, "aliases": ["rivière des pluies", "riviere des pluies", "rivières des pluies", "rivieres des pluies", "rdp"]},
    "Sainte-Clotilde": {"commune": "Saint-Denis", "lat": -20.8993896, "lon": 55.4765669, "aliases": ["sainte clotilde", "ste clotilde", "sainte-clotilde", "sainte coltilde", "ste coltilde"]},
    "Moufia": {"commune": "Saint-Denis", "lat": -20.9049830, "lon": 55.4841971, "aliases": ["moufia", "universite du moufia", "université du moufia"]},
    "Domenjod": {"commune": "Saint-Denis", "lat": -20.9156324, "lon": 55.5066020, "aliases": ["domenjod"]},
    "Primat": {"commune": "Saint-Denis", "lat": -20.8988039, "lon": 55.5018099, "aliases": ["primat"]},
    "Le Chaudron": {"commune": "Saint-Denis", "lat": -20.8889816, "lon": 55.4901534, "aliases": ["le chaudron", "chaudron"]},
    "Montgaillard": {"commune": "Saint-Denis", "lat": -20.9051735, "lon": 55.4671111, "aliases": ["montgaillard"]},
    "Bellepierre": {"commune": "Saint-Denis", "lat": -20.9040237, "lon": 55.4447822, "aliases": ["bellepierre"]},
    "Champ Fleuri": {"commune": "Saint-Denis", "lat": -20.8937, "lon": 55.4617, "aliases": ["champ fleuri", "champ-fleuri"]},
    "Les Camélias": {"commune": "Saint-Denis", "lat": -20.8985, "lon": 55.4575, "aliases": ["les camelias", "camelia", "camélias", "camelias"]},
    "La Source": {"commune": "Saint-Denis", "lat": -20.8909, "lon": 55.4533, "aliases": ["quartier la source", "secteur la source"]},
    "Providence": {"commune": "Saint-Denis", "lat": -20.8964, "lon": 55.4517, "aliases": ["providence"]},
    "Bas de la Rivière": {"commune": "Saint-Denis", "lat": -20.8748, "lon": 55.4434, "aliases": ["bas de la rivière", "bas de la riviere"]},
    "La Montagne": {"commune": "Saint-Denis", "lat": -20.8980, "lon": 55.4030, "aliases": ["la montagne", "montagne"]},
    "Duparc": {"commune": "Sainte-Marie", "lat": -20.8990033, "lon": 55.5210549, "aliases": ["duparc", "du parc", "centre commercial duparc", "cc duparc"]},
    "La Grande Montée": {"commune": "Sainte-Marie", "lat": -20.9235086, "lon": 55.5188513, "aliases": ["la grande montée", "grande montée", "la grande montee", "grande montee"]},
    "Beauséjour": {"commune": "Sainte-Marie", "lat": -20.9208911, "lon": 55.5289973, "aliases": ["beauséjour", "beausejour", "beau sejour"]},
    "La Convenance": {"commune": "Sainte-Marie", "lat": -20.8953854, "lon": 55.5683080, "aliases": ["la convenance", "convenance"]},
    "La Ressource": {"commune": "Sainte-Marie", "lat": -20.9168, "lon": 55.5312, "aliases": ["la ressource", "ressource"]},
    "Ravine des Chèvres": {"commune": "Sainte-Marie", "lat": -20.9180, "lon": 55.5500, "aliases": ["ravine des chèvres", "ravine des chevres"]},
    "Gillot": {"commune": "Sainte-Marie", "lat": -20.8910, "lon": 55.5165, "aliases": ["gillot", "aeroport gillot", "aéroport gillot"]},
}

ALIAS_TO_COMMUNE: list[tuple[str, str]] = []
for commune, meta in COMMUNES.items():
    aliases = [commune] + meta["aliases"]
    for a in aliases:
        ALIAS_TO_COMMUNE.append((norm(a), commune))
ALIAS_TO_COMMUNE.sort(key=lambda x: len(x[0]), reverse=True)

DISTRICT_ALIAS_TO_NAME: list[tuple[str, str]] = []
for name, meta in DISTRICTS.items():
    for a in [name] + meta["aliases"]:
        DISTRICT_ALIAS_TO_NAME.append((norm(a), name))
DISTRICT_ALIAS_TO_NAME.sort(key=lambda x: len(x[0]), reverse=True)


def load_payload() -> dict[str, Any]:
    data = json.loads(LISTINGS.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "listings" not in data:
        raise SystemExit(f"Unexpected listings shape: {LISTINGS}")
    return data


def price(it: dict[str, Any]) -> int | None:
    v = it.get("rent_eur", it.get("price"))
    try:
        return int(round(float(v))) if v not in (None, "") else None
    except Exception:
        return None


def surface(it: dict[str, Any]) -> float | None:
    v = it.get("surface_m2", it.get("surface"))
    try:
        return float(v) if v not in (None, "") else None
    except Exception:
        return None


def text_blob(it: dict[str, Any]) -> str:
    return norm(" ".join(str(it.get(k) or "") for k in ["title", "city", "commune", "district", "location", "location_label", "primary_zone", "description", "region"]))


def infer_location(it: dict[str, Any]) -> dict[str, Any]:
    blob = text_blob(it)
    declared = it.get("commune") or it.get("city")
    declared_norm = norm(declared)

    district_name = None
    district_alias = None
    # Some listings mention both a broad sector (Sainte-Clotilde) and a finer
    # lieu-dit/quartier (e.g. "Sainte Clotilde LA BRETAGNE"). Prefer the finer
    # signal so the public card/modal does not downgrade Bretagne to Sainte-Clotilde.
    fine_priority = ["La Bretagne", "Rivière des Pluies", "La Grande Montée", "Duparc", "Beauséjour", "La Convenance"]
    for fine_name in fine_priority:
        for alias in [fine_name] + DISTRICTS[fine_name]["aliases"]:
            alias_n = norm(alias)
            if alias_n and re.search(rf"(^|\b){re.escape(alias_n)}(\b|$)", blob):
                district_name = fine_name
                district_alias = alias_n
                break
        if district_name:
            break
    if not district_name:
        for alias, name in DISTRICT_ALIAS_TO_NAME:
            if alias and re.search(rf"(^|\b){re.escape(alias)}(\b|$)", blob):
                district_name = name
                district_alias = alias
                break

    hits: list[tuple[str, str]] = []
    if district_name:
        hits.append((district_alias or norm(district_name), DISTRICTS[district_name]["commune"]))
    else:
        for alias, commune in ALIAS_TO_COMMUNE:
            if not alias:
                continue
            if re.search(rf"(^|\b){re.escape(alias)}(\b|$)", blob):
                hits.append((alias, commune))
                break
    if declared_norm:
        for alias, commune_declared in ALIAS_TO_COMMUNE:
            if declared_norm == alias:
                if not hits:
                    hits.insert(0, (alias, commune_declared))
                break

    commune = hits[0][1] if hits else (declared if declared and declared != "Non précisée" else None)
    meta = COMMUNES.get(commune or "", {})
    confidence = 0.35
    method = "region_only_or_unknown"
    if district_name:
        confidence = 0.90 if declared and norm(DISTRICTS[district_name]["commune"]) == norm(declared) else 0.82
        method = "district_alias_match"
    elif hits and declared and norm(commune) == norm(declared):
        confidence = 0.94
        method = "declared_commune_confirmed_by_text"
    elif declared and commune and norm(commune) == norm(declared):
        confidence = 0.82
        method = "declared_commune"
    elif hits:
        confidence = 0.72
        method = "text_alias_match"
    elif it.get("region"):
        confidence = 0.45
        method = "region_only"

    district_hits = []
    if district_name:
        district_hits.append(district_name)
    if commune and commune in COMMUNES:
        for a in COMMUNES[commune]["aliases"]:
            an = norm(a)
            if an and an != norm(commune) and re.search(rf"(^|\b){re.escape(an)}(\b|$)", blob):
                district_hits.append(a)
    quality = "haute" if confidence >= .85 else "moyenne" if confidence >= .65 else "faible"
    map_point = None
    if district_name:
        d = DISTRICTS[district_name]
        map_point = {
            "lat": d["lat"],
            "lon": d["lon"],
            "zoom": 15,
            "label": f"{district_name} · {d['commune']}",
            "precision": "quartier/lieu-dit approximatif, pas adresse exacte",
            "osm_url": f"https://www.openstreetmap.org/?mlat={d['lat']}&mlon={d['lon']}#map=15/{d['lat']}/{d['lon']}",
        }
    precise_label = f"{district_name} · {commune}" if district_name and commune else (commune or "Non précisée")
    return {
        "commune_inferred": commune or "Non précisée",
        "region_inferred": meta.get("region") or it.get("region") or "Région non précisée",
        "interco": meta.get("interco") or "Non précisé",
        "district_best": district_name,
        "district_hints": sorted(set(district_hits))[:4],
        "precise_location_label": precise_label,
        "map_point": map_point,
        "north_east_focus": commune in NORTH_EAST_PRIORITY,
        "confidence": round(confidence, 2),
        "quality": quality,
        "method": method,
    }


def signature(it: dict[str, Any]) -> str:
    p = price(it) or 0
    s = surface(it) or 0
    loc = it.get("location_intelligence", {}).get("commune_inferred") or it.get("city") or ""
    title_words = " ".join(sorted(w for w in norm(it.get("title")).split() if len(w) >= 4)[:8])
    return f"{norm(loc)}|{round(p/25)*25}|{round(s/5)*5}|{title_words}"


def similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    pa, pb = price(a), price(b)
    sa, sb = surface(a), surface(b)
    la = a.get("location_intelligence", {}).get("commune_inferred") or a.get("city")
    lb = b.get("location_intelligence", {}).get("commune_inferred") or b.get("city")
    score = 0.0
    if la and lb and norm(la) == norm(lb): score += 0.26
    if pa and pb:
        score += max(0, 1 - abs(pa-pb)/max(pa,pb,1)) * 0.24
    if sa and sb:
        score += max(0, 1 - abs(sa-sb)/max(sa,sb,1)) * 0.22
    score += SequenceMatcher(None, norm(a.get("title")), norm(b.get("title"))).ratio() * 0.20
    if a.get("rooms") and b.get("rooms") and a.get("rooms") == b.get("rooms"): score += 0.08
    return round(min(score, 1.0), 3)


def build_dedup(items: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        p = price(it); s = surface(it)
        if not p or not s:
            continue
        loc = norm(it.get("location_intelligence", {}).get("commune_inferred") or it.get("city"))
        buckets[f"{loc}|{round(p/100)*100}|{round(s/10)*10}|{it.get('rooms') or ''}"].append(it)
    groups=[]; group_id=1
    for bucket_items in buckets.values():
        if len(bucket_items) < 2: continue
        used=set()
        for i,a in enumerate(bucket_items):
            if a.get("id") in used: continue
            members=[a]
            for b in bucket_items[i+1:]:
                if b.get("id") in used: continue
                sim=similarity(a,b)
                if sim >= 0.78 and (a.get("url") != b.get("url") or a.get("source") != b.get("source")):
                    members.append(b)
                    used.add(b.get("id"))
            if len(members) >= 2:
                sources=sorted(set(str(m.get("source") or m.get("source_site") or "?") for m in members))
                canonical=max(members, key=lambda x: ((1 if x.get("local_image_url") else 0), len(str(x.get("description") or "")), x.get("score") or 0))
                gid=f"D{group_id:04d}"; group_id+=1
                groups.append({
                    "group_id": gid,
                    "confidence": max(similarity(members[0], m) for m in members[1:]),
                    "canonical_id": canonical.get("id"),
                    "member_ids": [m.get("id") for m in members],
                    "sources": sources,
                    "links": [{"source": m.get("source") or m.get("source_site"), "url": m.get("url"), "id": m.get("id")} for m in members],
                    "title": canonical.get("title"),
                    "price": price(canonical),
                    "surface": surface(canonical),
                    "location": canonical.get("location") or canonical.get("city"),
                    "policy": "fusion douce: on conserve toutes les annonces et liens; aucun écrasement de champ source",
                })
                for m in members: used.add(m.get("id"))
    by_id={mid:g for g in groups for mid in g["member_ids"]}
    for it in items:
        g=by_id.get(it.get("id"))
        if g:
            it["dedup_group_id"]=g["group_id"]
            it["dedup_sources"]=g["sources"]
            it["dedup_alternative_links"]=g["links"]
    return {"generated_at": now(), "groups_count": len(groups), "groups": groups}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile_rank(values: list[float], v: float) -> float:
    if not values: return .5
    return sum(1 for x in values if x <= v) / len(values)


def opportunity(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_commune=defaultdict(list)
    for it in items:
        p=price(it); s=surface(it)
        if p and s and s>0:
            commune=it.get("location_intelligence",{}).get("commune_inferred") or it.get("city") or "Non précisée"
            by_commune[commune].append(p/s)
    medians={c: statistics.median(vals) for c,vals in by_commune.items() if len(vals)>=3}
    global_vals=[v for vals in by_commune.values() for v in vals]
    global_med=statistics.median(global_vals) if global_vals else None
    scored=[]
    for it in items:
        p=price(it); s=surface(it); commune=it.get("location_intelligence",{}).get("commune_inferred") or it.get("city") or "Non précisée"
        ppm=(p/s) if p and s and s>0 else None
        ref=medians.get(commune) or global_med
        reasons=[]; warnings=[]; score=50
        if ppm and ref:
            delta=(ref-ppm)/ref
            if delta>0:
                score += min(30, delta*80); reasons.append(f"prix/m² {delta*100:.0f}% sous la médiane observée du secteur")
            else:
                score += max(-25, delta*55); warnings.append(f"prix/m² au-dessus de la médiane observée du secteur ({ppm:.1f} €/m²)")
        if it.get("local_image_url"): score+=6; reasons.append("photo principale locale disponible")
        else: warnings.append("photo locale absente")
        desc_len=len(str(it.get("description") or ""))
        if desc_len>160: score+=5
        elif desc_len<40: score-=8; warnings.append("description pauvre")
        if it.get("rooms") and s and s/max(float(it.get("rooms") or 1),1) >= 18: score+=4; reasons.append("surface/pièce confortable")
        if it.get("dedup_group_id"): score-=3; warnings.append("doublon probable: vérifier les liens sources")
        if p and p>3500: score-=8; warnings.append("loyer élevé: comparer au marché local")
        score=max(0,min(100,round(score)))
        label="opportunité forte" if score>=75 else "à étudier" if score>=58 else "standard" if score>=42 else "risque/bruit"
        analysis={
            "score": score,
            "label": label,
            "price_per_m2": round(ppm,2) if ppm else None,
            "sector_median_price_per_m2": round(ref,2) if ref else None,
            "reasons": reasons[:5] or ["données suffisantes pour comparaison simple, pas de signal fort"],
            "warnings": warnings[:5],
            "method": "score heuristique local: prix/m² vs médiane observée, complétude annonce, photo locale, surface/pièce, doublons",
        }
        it["opportunity_analysis"]=analysis
        scored.append({"id": it.get("id"), "title": it.get("title"), "source": it.get("source") or it.get("source_site"), "url": it.get("url"), "location": it.get("location") or commune, **analysis})
    return {"generated_at": now(), "global_median_price_per_m2": round(global_med,2) if global_med else None, "commune_medians": {k: round(v,2) for k,v in sorted(medians.items())}, "top": sorted(scored, key=lambda x: x["score"], reverse=True)[:80]}


def render_page(title: str, subtitle: str, body: str) -> str:
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>
:root{{--bg:#f6f3ee;--paper:#fffdf8;--ink:#20201d;--muted:#6f6a61;--line:#e6dfd3;--brand:#0f766e;--ok:#15803d;--warn:#b45309;--bad:#b91c1c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;line-height:1.5}}main{{max-width:1120px;margin:auto;padding:24px 16px 70px}}a{{color:var(--brand)}}.top{{display:flex;gap:12px;align-items:center;justify-content:space-between;margin-bottom:22px}}.btn{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:10px 14px;text-decoration:none;background:#fff}}.hero,.card{{background:var(--paper);border:1px solid var(--line);border-radius:22px;box-shadow:0 16px 40px rgba(55,45,30,.08)}}.hero{{padding:26px;margin-bottom:18px}}h1{{font-size:clamp(28px,5vw,52px);line-height:1;letter-spacing:-.06em;margin:0 0 10px}}h2{{letter-spacing:-.03em}}.muted{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}.card{{padding:16px;min-width:0}}.pill{{display:inline-block;border-radius:999px;padding:5px 9px;margin:3px;background:#eef7f5;color:#115e59;font-size:12px;font-weight:750}}.bad{{color:var(--bad)}}.ok{{color:var(--ok)}}.warn{{color:var(--warn)}}pre{{white-space:pre-wrap;background:#151515;color:#fafafa;border-radius:16px;padding:14px;overflow:auto}}table{{width:100%;border-collapse:collapse;background:#fff;border-radius:16px;overflow:hidden}}td,th{{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}}@media(max-width:650px){{.top{{display:block}}}}
</style></head><body><main><div class='top'><a class='btn' href='index.html'>← Portail</a><div><a class='btn' href='changes.html'>Veille</a> <a class='btn' href='source_health.html'>Sources</a> <a class='btn' href='dedup.html'>Doublons</a> <a class='btn' href='locations.html'>Localisation</a></div></div><section class='hero'><h1>{html.escape(title)}</h1><p class='muted'>{html.escape(subtitle)}</p></section>{body}</main></body></html>"""


def write_outputs(payload: dict[str, Any], dedup: dict[str, Any], opp: dict[str, Any]) -> None:
    items=payload["listings"]
    loc_counts=Counter(i["location_intelligence"]["commune_inferred"] for i in items)
    loc_quality=Counter(i["location_intelligence"]["quality"] for i in items)
    locations={"generated_at": now(), "communes_reference": COMMUNES, "summary": {"listings": len(items), "quality": dict(loc_quality), "top_communes": loc_counts.most_common(30), "north_east_listings": sum(1 for i in items if i["location_intelligence"].get("north_east_focus"))}, "listings": [{"id": i.get("id"), "title": i.get("title"), "source": i.get("source") or i.get("source_site"), "url": i.get("url"), **i["location_intelligence"]} for i in items]}
    (APP/"locations.json").write_text(json.dumps(locations, ensure_ascii=False, indent=2), encoding="utf-8")
    (APP/"dedup_groups.json").write_text(json.dumps(dedup, ensure_ascii=False, indent=2), encoding="utf-8")
    (APP/"opportunity.json").write_text(json.dumps(opp, ensure_ascii=False, indent=2), encoding="utf-8")

    cards="<div class='grid'>"+"".join(f"<div class='card'><b>{html.escape(c)}</b><br><span class='muted'>{n} annonces</span></div>" for c,n in loc_counts.most_common(12))+"</div>"
    north="<ul>"+"".join(f"<li><b>{html.escape(c)}</b> — {COMMUNES[c]['interco']} / {COMMUNES[c]['region']} — quartiers/alias utiles: {html.escape(', '.join(COMMUNES[c]['aliases'][:8]))}</li>" for c in ["Saint-Denis","Sainte-Marie","Sainte-Suzanne","Saint-André","Bras-Panon","Saint-Benoît"])+"</ul>"
    loc_body=f"<section class='card'><h2>Ce que j’ai compris de La Réunion</h2><p>La lecture utile pour l’immo n’est pas seulement Nord/Sud/Est/Ouest: il faut mapper communes, intercommunalités et micro-quartiers. Pour ton besoin Nord / Nord‑Est, le couloir prioritaire est CINOR puis entrée CIREST: Saint‑Denis, Sainte‑Marie, Sainte‑Suzanne, Saint‑André, Bras‑Panon, Saint‑Benoît.</p>{north}</section><section class='card'><h2>Lecture des annonces actuelles</h2><p>Qualité inférée: {dict(loc_quality)}. Annonces Nord/Nord‑Est prioritaires: {locations['summary']['north_east_listings']} / {len(items)}.</p>{cards}</section><section class='card'><h2>Algorithme carte recommandé</h2><ol><li>Normaliser accents/tirets/abréviations St/Ste.</li><li>Détecter commune déclarée puis confirmer par titre/description/quartier.</li><li>Si quartier reconnu, placer sur centroïde quartier; sinon centroïde commune.</li><li>Afficher une précision: haute / moyenne / faible, pour ne pas mentir sur une adresse absente.</li><li>Pour les points personnels (travail/école/famille), calculer distance seulement si la précision est au moins moyenne.</li></ol></section>"
    (APP/"locations.html").write_text(render_page("Localisation Réunion — analyse des annonces", "Page séparée: carte et compréhension géographique sans alourdir l’accueil.", loc_body), encoding="utf-8")

    dedup_rows="".join(f"<div class='card'><h2>{html.escape(g['group_id'])} · {html.escape(g.get('title') or '')}</h2><p><b>{g.get('price') or 'prix n.c.'}€</b> · {g.get('surface') or 'surface n.c.'}m² · sources: {html.escape(', '.join(g['sources']))} · confiance {g['confidence']}</p><ul>"+"".join(f"<li>{html.escape(str(l.get('source')))} — <a href='{html.escape(str(l.get('url') or ''))}' target='_blank'>source</a></li>" for l in g['links'])+"</ul></div>" for g in dedup["groups"][:60]) or "<p>Aucun groupe fort détecté.</p>"
    dedup_body=f"<section class='card'><h2>Politique anti-perte</h2><p>Déduplication douce: on signale des groupes probables, mais on ne supprime rien. La fiche canonique peut servir à l’affichage futur, tout en conservant tous les liens sources.</p><p>Groupes détectés: <b>{dedup['groups_count']}</b></p></section><div class='grid'>{dedup_rows}</div>"
    (APP/"dedup.html").write_text(render_page("Doublons probables — fusion douce", "Évite le bruit sans perdre les informations ni les liens sources.", dedup_body), encoding="utf-8")

    opp_cards="<div class='grid'>"+"".join(f"<div class='card'><b>{o['score']}/100 · {html.escape(o['label'])}</b><p>{html.escape(o.get('title') or '')}</p><p class='muted'>{html.escape(o.get('location') or '')} · {o.get('price_per_m2') or '?'} €/m²</p><ul>"+"".join(f"<li>{html.escape(r)}</li>" for r in o.get('reasons',[])[:3])+f"</ul><a href='{html.escape(o.get('url') or '')}' target='_blank'>Source</a></div>" for o in opp["top"][:24])+"</div>"
    opp_body=f"<section class='card'><h2>Important</h2><p>Ce score est une aide au tri, pas une estimation notariale. Il compare les annonces entre elles dans notre base fraîche: prix/m² local, complétude, photos, doublons et signaux pauvres/suspects.</p></section>{opp_cards}"
    (APP/"opportunity.html").write_text(render_page("Scores opportunité — assistant immo", "Bouton discret sur les cards + page séparée pour les meilleures pistes.", opp_body), encoding="utf-8")

    course_alerts="""
<section class='card'><h2>Objectif</h2><p>Transformer le portail en veille active: refresh quotidien/biquotidien, historique des annonces, nouvelles annonces, disparitions, baisses de prix, sources cassées, puis alerte Telegram uniquement quand il y a un événement utile.</p></section>
<section class='card'><h2>Pipeline proposé</h2><ol><li><b>Refresh</b>: lancer les scrapers existants et rebuild statique.</li><li><b>Snapshot</b>: écrire chaque annonce dans SQLite history sans supprimer l’ancien état.</li><li><b>Diff</b>: comparer id/source/url/prix/statut avec le snapshot précédent.</li><li><b>Filtrage</b>: appliquer tes recherches sauvegardées mobile.</li><li><b>Notification</b>: Telegram si nouvelle annonce pertinente, baisse de prix, disparition ou source critique cassée.</li><li><b>Pages séparées</b>: changes.html, source_health.html, dedup.html, locations.html, opportunity.html.</li></ol></section>
<section class='card'><h2>Contrat anti-spam / anti-perte</h2><ul><li>Premier run = baseline silencieuse.</li><li>Les annonces disparues sont marquées inactives, jamais supprimées.</li><li>Une alerte source casse seulement si l’état est nouveau ou change; cooldown 24h sinon.</li><li>Les baisses/hausses gardent old_value/new_value/event_at.</li><li>Les doublons gardent tous les liens sources.</li></ul></section>
<section class='card'><h2>Pseudo-code</h2><pre>refresh_sources()
build_listings_json()
for listing in current_export:
    previous = history.get(listing.id)
    if not previous: event('new')
    elif previous.price != listing.price: event('price_changed')
    history.upsert_current(listing)
for previous_active not in current_ids:
    event('disappeared')
    mark_inactive()
for saved_search in config:
    matches = filter(current, saved_search)
    send_only_new_or_changed(matches)
check_source_health()
if critical_source_newly_bad: telegram()</pre></section>
"""
    (APP/"alertes_cours.html").write_text(render_page("Cours — alertes personnalisées immo", "Comment je compte implémenter l’assistant immo mobile: algorithmes, parsing, historique et Telegram.", course_alerts), encoding="utf-8")

    # Update listings with annotations after outputs.
    payload.setdefault("meta", {})["intelligence_layers"]={"generated_at": now(), "dedup_groups": dedup["groups_count"], "opportunity_top": len(opp["top"]), "location_quality": dict(loc_quality)}
    LISTINGS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    payload=load_payload(); items=payload["listings"]
    for it in items:
        it["location_intelligence"]=infer_location(it)
        # photo status for transparent premium UX
        local_gallery=it.get("local_image_urls") or ([it.get("local_image_url")] if it.get("local_image_url") else [])
        external_gallery=it.get("image_urls") or ([it.get("image_url")] if it.get("image_url") else [])
        it["photo_status"]={"local_count": len([x for x in local_gallery if x]), "external_count": len([x for x in external_gallery if x]), "origin": "local" if it.get("local_image_url") else "external" if it.get("image_url") else "missing", "premium_gallery_ready": len([x for x in local_gallery if x]) > 1}
    dedup=build_dedup(items)
    opp=opportunity(items)
    write_outputs(payload, dedup, opp)
    print(json.dumps({"ok": True, "listings": len(items), "dedup_groups": dedup["groups_count"], "top_opportunity": opp["top"][:3], "outputs": ["locations.html", "dedup.html", "opportunity.html", "alertes_cours.html"]}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
