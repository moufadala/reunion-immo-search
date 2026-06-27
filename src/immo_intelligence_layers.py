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
    "Saint-Paul": {"region": "Ouest", "interco": "TCO", "aliases": ["st paul", "saint paul", "plateau caillou", "boucan canot", "saint gilles", "st gilles", "la saline", "l'ermitage", "hermitage", "bois de nefles saint paul", "la saline les bains", "saline les bains", "97434", "trou d'eau", "trou deau", "cambaie"]},
    "Le Port": {"region": "Ouest", "interco": "TCO", "aliases": ["le port", "port"]},
    "La Possession": {"region": "Ouest", "interco": "TCO", "aliases": ["la possession", "possession", "ravine a malheur", "dos d'ane", "moulin joli"]},
    "Trois-Bassins": {"region": "Ouest", "interco": "TCO", "aliases": ["trois bassins", "3 bassins"]},
    "Saint-Leu": {"region": "Ouest", "interco": "TCO", "aliases": ["st leu", "saint leu", "piton saint leu", "la fontaine", "etang saint leu"]},
    "Saint-Pierre": {"region": "Sud", "interco": "CIVIS", "aliases": ["st pierre", "saint pierre", "terre sainte", "ravine blanche", "bois d'olives", "ligne paradis", "grands bois", "ravine des cabris", "ligne des bambous", "bassin plat", "conde concession", "condé concession"]},
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
    "Bois de Nèfles Sainte-Clotilde": {"commune": "Saint-Denis", "lat": -20.9024, "lon": 55.5010, "aliases": ["bois de nèfles sainte clotilde", "bois de nefles sainte clotilde", "bois de nèfles ste clotilde", "bois de nefles ste clotilde", "sainte clotilde bois de nèfles", "sainte clotilde bois de nefles", "ste clotilde bois de nèfles", "ste clotilde bois de nefles", "quartier bois de nèfles", "quartier bois de nefles"]},
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
    "La Montagne": {"commune": "Saint-Denis", "lat": -20.8980, "lon": 55.4030, "aliases": ["la montagne", "saint-denis la montagne", "saint denis la montagne"]},
    "Duparc": {"commune": "Sainte-Marie", "lat": -20.8990033, "lon": 55.5210549, "aliases": ["duparc", "du parc", "centre commercial duparc", "cc duparc"]},
    "Les Cafés": {"commune": "Sainte-Marie", "lat": -20.9017842, "lon": 55.5681864, "aliases": ["les cafés", "les cafes", "quartier les cafés", "quartier les cafes"]},
    "La Grande Montée": {"commune": "Sainte-Marie", "lat": -20.9235086, "lon": 55.5188513, "aliases": ["la grande montée", "grande montée", "la grande montee", "grande montee"]},
    # Keep aliases to proper toponyms only. The generic phrase "beau séjour"
    # appears in many descriptions as a nice living room and previously caused
    # false Beauséjour/Sainte-Marie matches (e.g. Saint-Gilles listings).
    "Beauséjour": {"commune": "Sainte-Marie", "lat": -20.9208911, "lon": 55.5289973, "aliases": ["beauséjour", "beausejour"]},
    "La Convenance": {"commune": "Sainte-Marie", "lat": -20.8953854, "lon": 55.5683080, "aliases": ["la convenance", "convenance"]},
    "La Ressource": {"commune": "Sainte-Marie", "lat": -20.9168, "lon": 55.5312, "aliases": ["la ressource", "ressource"]},
    "Ravine des Chèvres": {"commune": "Sainte-Marie", "lat": -20.9180, "lon": 55.5500, "aliases": ["ravine des chèvres", "ravine des chevres"]},
    "Gillot": {"commune": "Sainte-Marie", "lat": -20.8910, "lon": 55.5165, "aliases": ["gillot", "aeroport gillot", "aéroport gillot"]},
    "La Saline les Bains": {"commune": "Saint-Paul", "lat": -21.0940, "lon": 55.2385, "aliases": ["la saline les bains", "saline les bains", "la-saline-les-bains", "97434"]},
    "Trou d'Eau": {"commune": "Saint-Paul", "lat": -21.1045, "lon": 55.2450, "aliases": ["trou d'eau", "trou deau", "trou-d-eau", "trou-d'eau"]},
    "Cambaie": {"commune": "Saint-Paul", "lat": -20.9745, "lon": 55.2960, "aliases": ["cambaie", "secteur de cambaie"]},
    "Bois de Nèfles Saint-Paul": {"commune": "Saint-Paul", "lat": -21.0080, "lon": 55.3300, "aliases": ["bois de nefles saint paul", "bois de nèfles saint paul", "bois de nefles st paul", "bdn st paul", "bdn saint paul"]},
    "Ravine des Cabris": {"commune": "Saint-Pierre", "lat": -21.2770, "lon": 55.4780, "aliases": ["ravine des cabris", "quartier de la ravine des cabris", "concession ravine des cabris"]},
    "Ligne des Bambous": {"commune": "Saint-Pierre", "lat": -21.3020, "lon": 55.4690, "aliases": ["ligne des bambous", "saint-pierre ligne des bambous", "quartier de la ligne des bambous"]},
    "Bassin Plat": {"commune": "Saint-Pierre", "lat": -21.3050, "lon": 55.5150, "aliases": ["bassin plat", "quartier de bassin plat", "secteur de bassin plat", "b.plat", "b plat"]},
    "Condé-Concession": {"commune": "Saint-Pierre", "lat": -21.2960, "lon": 55.4860, "aliases": ["conde concession", "condé concession", "condé-concession", "conde-concession"]},
    "Moulin Joli": {"commune": "La Possession", "lat": -20.9320, "lon": 55.3420, "aliases": ["moulin joli"]},
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
    # Use source-facing text only. `district`/`location`/`city` may already be
    # derived by a previous intelligence pass; feeding them back into inference
    # makes bad geocoding self-reinforcing.
    return norm(" ".join(str(it.get(k) or "") for k in ["title", "description", "url", "commune", "region"]))


def title_location_blob(it: dict[str, Any]) -> str:
    """High-confidence source location hints: title and source URL."""
    return norm(" ".join(str(it.get(k) or "") for k in ["title", "url"]))


def first_commune_alias(blob: str) -> tuple[str, str] | None:
    for alias, commune in ALIAS_TO_COMMUNE:
        if alias and re.search(rf"(^|\b){re.escape(alias)}(\b|$)", blob):
            return (alias, commune)
    return None


def alias_in_blob(alias: str, blob: str) -> bool:
    return bool(alias and re.search(rf"(^|\b){re.escape(alias)}(\b|$)", blob))


def la_montagne_alias_allowed(alias: str, blob: str, strong_blob: str, declared_norm: str) -> bool:
    if alias != "la montagne":
        return True
    if re.search(r"\b(vue|vues|mer|ocean|oc[eé]an|panorama|panoramique|route|routes|chemin|sur)\b(?:\s+\w+){0,4}\s+la montagne\b", blob):
        return False
    if alias_in_blob(alias, strong_blob):
        return True
    return declared_norm in {"saint denis", "st denis"}


def district_alias_allowed(name: str, alias: str, blob: str, strong_blob: str, declared_norm: str) -> bool:
    if name == "La Montagne":
        return la_montagne_alias_allowed(alias, blob, strong_blob, declared_norm)
    return True


def infer_location(it: dict[str, Any]) -> dict[str, Any]:
    blob = text_blob(it)
    strong_blob = title_location_blob(it)
    declared = it.get("commune") or it.get("city")
    declared_norm = norm(declared)

    district_name = None
    district_alias = None
    # Some listings mention both a broad sector (Sainte-Clotilde) and a finer
    # lieu-dit/quartier (e.g. "Sainte Clotilde LA BRETAGNE"). Prefer the finer
    # signal so the public card/modal does not downgrade Bretagne to Sainte-Clotilde.
    fine_priority = [
        "Bois de Nèfles Sainte-Clotilde",
        # Prefer fine Saint-Denis districts over the broad Sainte-Clotilde sector
        # when both appear in the same source text (e.g. "Sainte-Clotilde Moufia").
        "Moufia",
        "Les Camélias",
        "Domenjod",
        "Champ Fleuri",
        "Providence",
        "Bas de la Rivière",
        "Montgaillard",
        "Bellepierre",
        "La Bretagne",
        "Rivière des Pluies",
        "La Grande Montée",
        "Duparc",
        "Les Cafés",
        "Beauséjour",
        "La Convenance",
        "La Saline les Bains",
        "Trou d'Eau",
        "Cambaie",
        "Bois de Nèfles Saint-Paul",
        "Ravine des Cabris",
        "Ligne des Bambous",
        "Bassin Plat",
        "Condé-Concession",
        "Moulin Joli",
    ]
    for fine_name in fine_priority:
        for alias in [fine_name] + DISTRICTS[fine_name]["aliases"]:
            alias_n = norm(alias)
            if alias_in_blob(alias_n, blob) and district_alias_allowed(fine_name, alias_n, blob, strong_blob, declared_norm):
                district_name = fine_name
                district_alias = alias_n
                break
        if district_name:
            break
    if not district_name:
        for alias, name in DISTRICT_ALIAS_TO_NAME:
            if alias_in_blob(alias, blob) and district_alias_allowed(name, alias, blob, strong_blob, declared_norm):
                district_name = name
                district_alias = alias
                break

    hits: list[tuple[str, str]] = []
    if district_name:
        hits.append((district_alias or norm(district_name), DISTRICTS[district_name]["commune"]))
    else:
        # Title/URL wins over a longer description. Agency descriptions may
        # mention nearby schools/towns; the source title normally carries the
        # actual advertised commune (e.g. "T2 à Sainte Marie" despite a later
        # paragraph mentioning Sainte-Suzanne).
        hit = first_commune_alias(strong_blob) or first_commune_alias(blob)
        if hit:
            hits.append(hit)
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
    display_district = "Bois de Nèfles" if district_name == "Bois de Nèfles Sainte-Clotilde" else district_name
    map_point = None
    if district_name:
        d = DISTRICTS[district_name]
        map_point = {
            "lat": d["lat"],
            "lon": d["lon"],
            "zoom": 15,
            "label": f"{display_district} · {d['commune']}",
            "precision": "quartier/lieu-dit approximatif, pas adresse exacte",
            "osm_url": f"https://www.openstreetmap.org/?mlat={d['lat']}&mlon={d['lon']}#map=15/{d['lat']}/{d['lon']}",
        }
    precise_label = f"{display_district} · {commune}" if display_district and commune else (commune or "Non précisée")
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


def normalized_url(it: dict[str, Any]) -> str:
    """Stable source URL for exact duplicate evidence.

    Strip volatile tracking/query/fragment while keeping the real path. Empty URLs
    are never treated as evidence.
    """
    raw = str(it.get("canonical_url") or it.get("url") or "").strip()
    if not raw:
        return ""
    raw = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return norm(raw)


def property_family(it: dict[str, Any]) -> str:
    text = norm(" ".join(str(it.get(k) or "") for k in ["property_type", "type", "title"]))
    if any(w in text for w in ["maison", "villa"]):
        return "maison"
    if any(w in text for w in ["appartement", "studio", "duplex", "t1", "t2", "t3", "t4", "t5", "chambre"]):
        return "appartement"
    return "unknown"


DEDUP_STOPWORDS = {
    "location", "appartement", "maison", "villa", "pieces", "piece", "saint", "sainte",
    "denis", "marie", "reunion", "974", "louer", "annonce", "immobilier", "m2", "dans",
    "avec", "pour", "une", "des", "les", "studio", "type", "appart", "centre", "ville",
}


def title_tokens(it: dict[str, Any]) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", norm(it.get("title")))
    return {w for w in words if w not in DEDUP_STOPWORDS and not w.isdigit()}


def token_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


def rel_close(x: float | int | None, y: float | int | None, pct: float, abs_tol: float) -> bool:
    if x is None or y is None:
        return False
    return abs(float(x) - float(y)) <= max(abs_tol, pct * max(abs(float(x)), abs(float(y)), 1.0))


def pair_dedup_decision(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Classify a pair with explicit evidence and conservative auto-hide rules.

    The old score alone was too permissive: generic titles like "Location
    Appartement · 1 pièce" can make distinct flats look identical. Auto-duplicate
    now requires exact URL evidence or a strong bundle of price/surface/rooms/type
    + title/token evidence. Otherwise the pair is kept as needs_review.
    """
    details = similarity_details(a, b)
    pa, pb = price(a), price(b)
    sa, sb = surface(a), surface(b)
    la = a.get("location_intelligence", {}).get("commune_inferred") or a.get("city")
    lb = b.get("location_intelligence", {}).get("commune_inferred") or b.get("city")
    same_commune = bool(la and lb and norm(la) == norm(lb))
    same_rooms = bool(a.get("rooms") and b.get("rooms") and a.get("rooms") == b.get("rooms"))
    same_family = property_family(a) == property_family(b) or "unknown" in {property_family(a), property_family(b)}
    same_source = str(a.get("source") or a.get("source_site") or "") == str(b.get("source") or b.get("source_site") or "")
    url_a, url_b = normalized_url(a), normalized_url(b)
    exact_url = bool(url_a and url_b and url_a == url_b)
    title_ratio = float(details.get("title_ratio") or 0)
    overlap = token_overlap(a, b)
    price_tight = rel_close(pa, pb, 0.03, 35)
    price_close = rel_close(pa, pb, 0.07, 70)
    surface_tight = rel_close(sa, sb, 0.04, 3)
    surface_close = rel_close(sa, sb, 0.10, 8)
    evidence: list[str] = []
    veto: list[str] = list(details.get("veto") or [])
    if exact_url:
        evidence.append("même URL source normalisée")
    if same_commune:
        evidence.append("même commune")
    else:
        veto.append("commune différente ou incertaine")
    if same_family:
        evidence.append("même famille de bien")
    else:
        veto.append("type de bien différent")
    if same_rooms:
        evidence.append("même nombre de pièces")
    elif a.get("rooms") and b.get("rooms"):
        veto.append("nombre de pièces différent")
    if price_tight:
        evidence.append("loyer très proche")
    elif price_close:
        evidence.append("loyer proche")
    if surface_tight:
        evidence.append("surface très proche")
    elif surface_close:
        evidence.append("surface proche")
    if title_ratio >= 0.88:
        evidence.append("titre très proche")
    elif title_ratio >= 0.70 or overlap >= 0.60:
        evidence.append("titre/tokens proches")

    # Exact URL is enough: mirrors OFIM RSS + OFIM and similar source aliases.
    if exact_url and same_commune:
        decision = "auto_duplicate"
        confidence = max(0.95, float(details["score"]))
        reason = "exact_url"
    # Strong fingerprint: conservative enough to hide from default grid.
    elif same_commune and same_family and same_rooms and price_tight and surface_tight and (title_ratio >= 0.82 or overlap >= 0.75) and not veto:
        decision = "auto_duplicate"
        confidence = max(0.88, float(details["score"]))
        reason = "strong_fingerprint"
    # Same-source generic near-matches are dangerous: keep review unless nearly exact.
    elif same_source and same_commune and same_rooms and price_tight and surface_tight and title_ratio >= 0.94 and not veto:
        decision = "auto_duplicate"
        confidence = max(0.86, float(details["score"]))
        reason = "same_source_near_exact"
    elif same_commune and same_family and price_close and surface_close and (same_rooms or not (a.get("rooms") and b.get("rooms"))) and float(details["score"]) >= 0.76 and not any(v.startswith("commune") for v in veto):
        decision = "needs_review"
        confidence = float(details["score"])
        reason = "similar_but_not_enough_for_auto_hide"
    else:
        decision = "distinct"
        confidence = float(details["score"])
        reason = "insufficient_evidence"
    return {
        "decision": decision,
        "confidence": round(min(confidence, 1.0), 3),
        "reason": reason,
        "score": details["score"],
        "reasons": evidence[:8] or list(details.get("reasons") or [])[:6],
        "veto": sorted(set(veto))[:6],
        "title_ratio": round(title_ratio, 3),
        "token_overlap": round(overlap, 3),
        "same_source": same_source,
        "price_delta": abs(pa - pb) if pa is not None and pb is not None else None,
        "surface_delta": round(abs(sa - sb), 2) if sa is not None and sb is not None else None,
    }


def signature(it: dict[str, Any]) -> str:
    p = price(it) or 0
    s = surface(it) or 0
    loc = it.get("location_intelligence", {}).get("commune_inferred") or it.get("city") or ""
    title_words = " ".join(sorted(w for w in norm(it.get("title")).split() if len(w) >= 4)[:8])
    return f"{norm(loc)}|{round(p/25)*25}|{round(s/5)*5}|{title_words}"


def similarity_details(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    pa, pb = price(a), price(b)
    sa, sb = surface(a), surface(b)
    la = a.get("location_intelligence", {}).get("commune_inferred") or a.get("city")
    lb = b.get("location_intelligence", {}).get("commune_inferred") or b.get("city")
    title_ratio = SequenceMatcher(None, norm(a.get("title")), norm(b.get("title"))).ratio()
    score = 0.0
    reasons=[]
    veto=[]
    if la and lb and norm(la) == norm(lb):
        score += 0.26; reasons.append("même commune inférée")
    elif la and lb:
        veto.append("communes différentes")
    if pa and pb:
        closeness=max(0, 1 - abs(pa-pb)/max(pa,pb,1))
        score += closeness * 0.24
        if closeness >= .95: reasons.append("prix quasi identique")
        elif closeness >= .85: reasons.append("prix proche")
    if sa and sb:
        closeness=max(0, 1 - abs(sa-sb)/max(sa,sb,1))
        score += closeness * 0.22
        if closeness >= .97: reasons.append("surface quasi identique")
        elif closeness >= .90: reasons.append("surface proche")
    score += title_ratio * 0.20
    if title_ratio >= .82: reasons.append("titre très proche")
    elif title_ratio >= .65: reasons.append("titre proche")
    if a.get("rooms") and b.get("rooms") and a.get("rooms") == b.get("rooms"):
        score += 0.08; reasons.append("même nombre de pièces")
    elif a.get("rooms") and b.get("rooms"):
        veto.append("nombre de pièces différent")
    score=round(min(score, 1.0), 3)
    return {"score": score, "reasons": reasons[:6], "veto": veto[:4], "title_ratio": round(title_ratio,3)}


def similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    return float(similarity_details(a, b)["score"])


def choose_public_canonical(members: list[dict[str, Any]]) -> dict[str, Any]:
    def rank(x: dict[str, Any]) -> tuple[int, int, int, int, str]:
        return (
            int(x.get("db_is_canonical") is not False),
            1 if x.get("local_image_url") else 0,
            len(str(x.get("description") or "")),
            int(x.get("score") or x.get("db_quality_score") or 0),
            str(x.get("id") or ""),
        )
    return sorted(members, key=rank, reverse=True)[0]


def build_dedup(items: list[dict[str, Any]]) -> dict[str, Any]:
    # Reset stale annotations so re-runs are deterministic.
    for it in items:
        for k in ["dedup_group_id", "dedup_sources", "dedup_decision", "dedup_confidence", "dedup_role", "dedup_reason"]:
            it.pop(k, None)

    candidate_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exact_url_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        p = price(it); s = surface(it)
        loc = norm(it.get("location_intelligence", {}).get("commune_inferred") or it.get("city"))
        if p and s:
            # Wide bucket: pair classifier performs the conservative decision.
            candidate_buckets[f"{loc}|{property_family(it)}|{round(p/100)*100}|{round(s/10)*10}|{it.get('rooms') or ''}"].append(it)
        u = normalized_url(it)
        if u:
            exact_url_buckets[u].append(it)

    edges: dict[tuple[str, str], dict[str, Any]] = {}
    by_id = {str(it.get("id")): it for it in items if it.get("id")}

    def add_edge(a: dict[str, Any], b: dict[str, Any], d: dict[str, Any]) -> None:
        ia, ib = str(a.get("id")), str(b.get("id"))
        if not ia or not ib or ia == ib:
            return
        key = (ia, ib) if ia < ib else (ib, ia)
        old = edges.get(key)
        if old is None or (d["decision"] == "auto_duplicate" and old.get("decision") != "auto_duplicate") or d.get("confidence", 0) > old.get("confidence", 0):
            edges[key] = {"a": key[0], "b": key[1], **d}

    exact_duplicate_ids: set[str] = set()
    for group in exact_url_buckets.values():
        if len(group) >= 2:
            exact_duplicate_ids.update(str(x.get("id")) for x in group if x.get("id"))

    # Exact URL groups first, independent from rounded price/surface buckets.
    for group in exact_url_buckets.values():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i+1:]:
                d = pair_dedup_decision(a, b)
                d["decision"] = "auto_duplicate"
                d["confidence"] = max(0.95, float(d.get("confidence") or 0))
                d["reason"] = "exact_url"
                add_edge(a, b, d)

    for bucket_items in candidate_buckets.values():
        if len(bucket_items) < 2:
            continue
        for i, a in enumerate(bucket_items):
            for b in bucket_items[i+1:]:
                # If an item already belongs to an exact same-URL duplicate set,
                # keep that strong source-alias group isolated. Otherwise one
                # ambiguous near-match to a third listing can downgrade the whole
                # connected component to needs_review and leak the exact duplicate
                # back into the default UI.
                if str(a.get("id")) in exact_duplicate_ids or str(b.get("id")) in exact_duplicate_ids:
                    continue
                d = pair_dedup_decision(a, b)
                if d["decision"] != "distinct":
                    add_edge(a, b, d)

    graph: dict[str, set[str]] = defaultdict(set)
    for (a, b), d in edges.items():
        graph[a].add(b); graph[b].add(a)
    seen: set[str] = set()
    groups=[]; group_id=1
    for node in sorted(graph):
        if node in seen:
            continue
        stack=[node]; comp=[]; seen.add(node)
        while stack:
            cur=stack.pop(); comp.append(cur)
            for nb in graph[cur]:
                if nb not in seen:
                    seen.add(nb); stack.append(nb)
        if len(comp) < 2:
            continue
        members=[by_id[mid] for mid in comp if mid in by_id]
        comp_edges=[d for key,d in edges.items() if key[0] in comp and key[1] in comp]
        auto_edges=[d for d in comp_edges if d.get("decision") == "auto_duplicate"]
        # Hide only groups whose connected evidence is all/mostly strong. Mixed
        # components remain reviewable to avoid losing distinct flats in same residence.
        decision = "auto_duplicate" if auto_edges and len(auto_edges) == len(comp_edges) else "needs_review"
        confidence = max(float(d.get("confidence") or 0) for d in comp_edges)
        canonical=choose_public_canonical(members)
        gid=f"D{group_id:04d}"; group_id+=1
        sources=sorted(set(str(m.get("source") or m.get("source_site") or "?") for m in members))
        explanations=[]
        for d in sorted(comp_edges, key=lambda x: float(x.get("confidence") or 0), reverse=True)[:6]:
            reasons = d.get("reasons") or []
            explanations.append(f"{d.get('a')} ↔ {d.get('b')}: {d.get('reason')} — " + "; ".join(reasons[:4]))
        groups.append({
            "group_id": gid,
            "confidence": round(confidence, 3),
            "decision": decision,
            "canonical_id": canonical.get("id"),
            "member_ids": sorted([m.get("id") for m in members if m.get("id")]),
            "sources": sources,
            "links": [{"source": m.get("source") or m.get("source_site"), "url": m.get("url"), "id": m.get("id"), "price": price(m), "surface": surface(m), "rooms": m.get("rooms"), "title": m.get("title")} for m in sorted(members, key=lambda x: str(x.get("id") or ""))],
            "title": canonical.get("title"),
            "price": price(canonical),
            "surface": surface(canonical),
            "location": canonical.get("location") or canonical.get("city"),
            "explanations": explanations[:6] or ["prix/surface/localisation proches dans le même bucket"],
            "pair_details": sorted(comp_edges, key=lambda x: float(x.get("confidence") or 0), reverse=True)[:12],
            "policy": "fusion douce: on conserve toutes les annonces et liens; aucune suppression DB; auto_duplicate seul est masqué de la grille par défaut; needs_review reste visible et traçable",
        })
    groups.sort(key=lambda g: (g["decision"] != "auto_duplicate", -float(g["confidence"]), g["group_id"]))
    by_member={mid:g for g in groups for mid in g["member_ids"]}
    for it in items:
        g=by_member.get(it.get("id"))
        if g:
            it["dedup_group_id"]=g["group_id"]
            it["dedup_sources"]=g["sources"]
            it["dedup_decision"]=g["decision"]
            it["dedup_confidence"]=g["confidence"]
            it["dedup_role"]="canonical" if it.get("id") == g.get("canonical_id") else "duplicate_or_variant"
            it["dedup_reason"]=g.get("explanations", [""])[0]
    return {
        "generated_at": now(),
        "version": "dedup-deep-v2-conservative-nondestructive",
        "groups_count": len(groups),
        "auto_duplicate_count": sum(1 for g in groups if g.get("decision") == "auto_duplicate"),
        "needs_review_count": sum(1 for g in groups if g.get("decision") == "needs_review"),
        "policy": "non destructive: raw listings stay in DB/listings.json; exact/strong auto duplicates are hidden only from default grid; ambiguous near matches remain visible as needs_review",
        "groups": groups,
    }


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile_rank(values: list[float], v: float) -> float:
    if not values: return .5
    return sum(1 for x in values if x <= v) / len(values)


def opportunity(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Explainable opportunity score v2.

    Community/research direction used here:
    - do not emit an opaque magic score;
    - split score into components;
    - separate business opportunity from data confidence;
    - only compare against local samples when sample size is credible;
    - keep all assumptions visible in the exported JSON.
    """
    by_commune_type: dict[str, list[float]] = defaultdict(list)
    by_commune: dict[str, list[float]] = defaultdict(list)
    for it in items:
        p=price(it); s=surface(it)
        if p and s and s>0:
            commune=it.get("location_intelligence",{}).get("commune_inferred") or it.get("city") or "Non précisée"
            typ=norm(it.get("property_type") or it.get("type") or "") or "type_nc"
            ppm=p/s
            by_commune[commune].append(ppm)
            by_commune_type[f"{commune}|{typ}"].append(ppm)

    medians_type={k: statistics.median(vals) for k,vals in by_commune_type.items() if len(vals)>=5}
    medians_commune={c: statistics.median(vals) for c,vals in by_commune.items() if len(vals)>=5}
    global_vals=[v for vals in by_commune.values() for v in vals]
    global_med=statistics.median(global_vals) if global_vals else None

    scored=[]
    for it in items:
        p=price(it); s=surface(it)
        commune=it.get("location_intelligence",{}).get("commune_inferred") or it.get("city") or "Non précisée"
        typ=norm(it.get("property_type") or it.get("type") or "") or "type_nc"
        ppm=(p/s) if p and s and s>0 else None
        sample_key=f"{commune}|{typ}"
        ref=medians_type.get(sample_key) or medians_commune.get(commune) or global_med
        ref_scope="commune+type" if sample_key in medians_type else "commune" if commune in medians_commune else "global"
        sample_size=len(by_commune_type.get(sample_key, [])) if sample_key in medians_type else len(by_commune.get(commune, [])) if commune in medians_commune else len(global_vals)

        components={"market_price": 0, "listing_quality": 0, "location_fit": 0, "freshness": 0, "risk": 0, "data_confidence": 0}
        reasons=[]; warnings=[]; assumptions=[]

        if ppm and ref:
            delta=(ref-ppm)/ref
            if delta >= .18:
                components["market_price"] = 34; reasons.append(f"prix/m² {delta*100:.0f}% sous la référence observée ({ref_scope}, n={sample_size})")
            elif delta >= .08:
                components["market_price"] = 27; reasons.append(f"prix/m² {delta*100:.0f}% sous la référence observée")
            elif delta >= -.05:
                components["market_price"] = 18; reasons.append("prix/m² proche de la référence observée")
            else:
                components["market_price"] = max(0, round(16 + delta*70)); warnings.append(f"prix/m² au-dessus de la référence observée ({ppm:.1f} €/m²)")
        else:
            assumptions.append("prix ou surface manquant: comparaison prix/m² impossible")

        local_count=len([x for x in (it.get("local_image_urls") or []) if x]) or (1 if it.get("local_image_url") else 0)
        desc_len=len(str(it.get("description") or ""))
        if local_count >= 3:
            components["listing_quality"] += 10; reasons.append(f"galerie locale {local_count} photos")
        elif local_count >= 1:
            components["listing_quality"] += 7; reasons.append("photo principale locale disponible")
        else:
            warnings.append("photo locale absente")
        if desc_len >= 350:
            components["listing_quality"] += 8; reasons.append("description source détaillée")
        elif desc_len >= 120:
            components["listing_quality"] += 5
        else:
            components["listing_quality"] -= 4; warnings.append("description courte")
        if it.get("rooms") and s and s/max(float(it.get("rooms") or 1),1) >= 18:
            components["listing_quality"] += 4; reasons.append("surface/pièce confortable")
        components["listing_quality"] = max(0, min(22, round(components["listing_quality"])))

        loc_quality=(it.get("location_intelligence") or {}).get("quality")
        if loc_quality == "haute":
            components["location_fit"] = 10; reasons.append("localisation inférée avec confiance haute")
        elif loc_quality == "moyenne":
            components["location_fit"] = 7
        else:
            components["location_fit"] = 3; warnings.append("localisation peu précise")
        if (it.get("location_intelligence") or {}).get("north_east_focus"):
            components["location_fit"] += 3
        components["location_fit"] = min(13, components["location_fit"])

        if it.get("is_new") or it.get("recent"):
            components["freshness"] = 5
        else:
            components["freshness"] = 3

        risk_penalty=0
        if it.get("dedup_group_id"):
            risk_penalty += 3; warnings.append("doublon probable: vérifier les liens sources")
        if p and p>3500:
            risk_penalty += 6; warnings.append("loyer élevé: comparer au marché local")
        if not p or not s:
            risk_penalty += 6; warnings.append("prix/surface incomplet")
        components["risk"] = max(0, 12-risk_penalty)

        confidence=0
        if sample_size >= 12: confidence += 4
        elif sample_size >= 5: confidence += 3
        elif sample_size >= 3: confidence += 2
        else: assumptions.append("peu de comparables locaux: score à prendre comme tri, pas valuation")
        if p and s: confidence += 2
        if desc_len >= 120: confidence += 2
        if local_count: confidence += 1
        if loc_quality in {"haute", "moyenne"}: confidence += 1
        components["data_confidence"] = min(10, confidence)

        score=max(0,min(100,round(sum(components.values()))))
        label="opportunité forte" if score>=75 else "à étudier" if score>=58 else "standard" if score>=42 else "risque/bruit"
        confidence_label="haute" if components["data_confidence"]>=8 else "moyenne" if components["data_confidence"]>=5 else "faible"
        analysis={
            "score": score,
            "score_version": "opportunity-v2-explainable-2026-06-25",
            "label": label,
            "confidence": confidence_label,
            "components": components,
            "price_per_m2": round(ppm,2) if ppm else None,
            "sector_median_price_per_m2": round(ref,2) if ref else None,
            "reference_scope": ref_scope,
            "reference_sample_size": sample_size,
            "reasons": reasons[:6] or ["pas de signal fort; annonce conservée comme piste standard"],
            "warnings": warnings[:6],
            "assumptions": assumptions[:5],
            "method": "score explicable v2: prix/m² vs référence locale si échantillon suffisant, qualité annonce, localisation, fraîcheur, risques et confiance séparée",
        }
        # Keep listings.json compact: the full explanation lives in opportunity.json.
        it["opportunity_analysis"]={
            "score": analysis["score"],
            "score_version": analysis["score_version"],
            "label": analysis["label"],
            "confidence": analysis["confidence"],
        }
        scored.append({"id": it.get("id"), "title": it.get("title"), "source": it.get("source") or it.get("source_site"), "url": it.get("url"), "location": it.get("location") or commune, **analysis})
    return {
        "generated_at": now(),
        "score_version": "opportunity-v2-explainable-2026-06-25",
        "global_median_price_per_m2": round(global_med,2) if global_med else None,
        "commune_medians": {k: round(v,2) for k,v in sorted(medians_commune.items())},
        "top": sorted(scored, key=lambda x: (x["score"], x.get("confidence") == "haute"), reverse=True)[:80],
    }

def render_page(title: str, subtitle: str, body: str) -> str:
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>
:root{{--bg:#f6f3ee;--paper:#fffdf8;--ink:#20201d;--muted:#6f6a61;--line:#e6dfd3;--brand:#0f766e;--ok:#15803d;--warn:#b45309;--bad:#b91c1c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;line-height:1.5}}main{{max-width:1120px;margin:auto;padding:16px 16px 70px}}a{{color:var(--brand)}}.top{{position:sticky;top:0;z-index:5;display:flex;gap:10px;align-items:center;justify-content:space-between;margin:0 -16px 14px;padding:10px 16px;background:rgba(246,243,238,.94);border-bottom:1px solid var(--line);backdrop-filter:blur(12px)}}.top div{{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}}.btn{{display:inline-flex;align-items:center;min-height:40px;border:1px solid var(--line);border-radius:999px;padding:8px 12px;text-decoration:none;background:#fff;font-weight:750}}.hero,.card,.sideFilters{{background:var(--paper);border:1px solid var(--line);border-radius:18px;box-shadow:0 10px 28px rgba(55,45,30,.07)}}.hero{{padding:18px;margin-bottom:12px}}h1{{font-size:clamp(26px,5vw,44px);line-height:1;letter-spacing:-.04em;margin:0 0 8px}}h2{{letter-spacing:-.03em}}.muted{{color:var(--muted)}}.sideFilters{{display:flex;gap:8px;align-items:center;margin-bottom:12px;padding:10px}}.sideFilters input{{flex:1;min-width:0;border:1px solid var(--line);border-radius:999px;background:#fff;min-height:44px;padding:10px 13px;font:inherit}}.sideFilters span{{color:var(--muted);font-size:13px;white-space:nowrap}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}}.card{{padding:13px;min-width:0}}.pill{{display:inline-block;border-radius:999px;padding:5px 9px;margin:3px;background:#eef7f5;color:#115e59;font-size:12px;font-weight:750}}.bad{{color:var(--bad)}}.ok{{color:var(--ok)}}.warn{{color:var(--warn)}}pre{{white-space:pre-wrap;background:#151515;color:#fafafa;border-radius:16px;padding:14px;overflow:auto}}table{{width:100%;border-collapse:collapse;background:#fff;border-radius:16px;overflow:hidden}}td,th{{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}}[hidden]{{display:none!important}}@media(max-width:650px){{main{{padding:0 10px 56px}}.top{{margin:0 -10px 10px;padding:8px 10px;align-items:flex-start}}.top div{{justify-content:flex-start;overflow:auto;flex-wrap:nowrap;padding-bottom:2px}}.btn{{white-space:nowrap;min-height:44px;padding:8px 11px}}.hero{{padding:13px;margin-bottom:9px}}h1{{font-size:24px;letter-spacing:-.03em}}.hero p{{font-size:13px;margin:0}}.sideFilters{{position:sticky;top:58px;z-index:4;margin-bottom:9px;padding:8px;border-radius:14px}}.sideFilters span{{display:none}}.grid{{grid-template-columns:1fr;gap:8px}}.card{{padding:11px;border-radius:14px}}.card h2{{font-size:16px;margin:3px 0 6px}}.card p,.card li{{font-size:13px}}td,th{{padding:8px;font-size:12px}}}}
</style></head><body><main><div class='top'><a class='btn' href='index.html'>← Portail</a><div><a class='btn' href='changes.html'>Veille</a> <a class='btn' href='source_health.html'>Sources</a> <a class='btn' href='dedup.html'>Doublons</a> <a class='btn' href='locations.html'>Localisation</a></div></div><section class='hero'><h1>{html.escape(title)}</h1><p class='muted'>{html.escape(subtitle)}</p></section><section class='sideFilters' aria-label='Filtrer cette page'><input id='sideQ' placeholder='Filtrer cette page…'><span id='sideCount'></span></section>{body}</main><script>const q=document.getElementById('sideQ'), cards=[...document.querySelectorAll('.card')], count=document.getElementById('sideCount');function n(s){{return String(s||'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase()}}function applySideFilter(){{const v=n(q?.value);let shown=0;cards.forEach(c=>{{const ok=!v||n(c.textContent).includes(v);c.hidden=!ok;if(ok)shown++;}});if(count)count.textContent=shown+' bloc(s)';}}q?.addEventListener('input',applySideFilter);applySideFilter();</script></body></html>"""


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

    dedup_rows="".join(
        f"<div class='card'><h2>{html.escape(g['group_id'])} · {html.escape(g.get('title') or '')}</h2>"
        f"<p><b>{g.get('price') or 'prix n.c.'}€</b> · {g.get('surface') or 'surface n.c.'}m² · sources: {html.escape(', '.join(g['sources']))} · confiance {g['confidence']} · décision {html.escape(g.get('decision',''))}</p>"
        f"<p class='muted'>Pourquoi: {html.escape(' / '.join(g.get('explanations') or [])[:260])}</p><ul>"
        +"".join(f"<li>{html.escape(str(l.get('source')))} — {l.get('price') or '?'}€ · {l.get('surface') or '?'}m² — <a href='{html.escape(str(l.get('url') or ''))}' target='_blank'>source</a></li>" for l in g['links'])
        +"</ul></div>" for g in dedup["groups"][:60]
    ) or "<p>Aucun groupe fort détecté.</p>"
    review_count=sum(1 for g in dedup.get('groups', []) if g.get('decision') == 'needs_review')
    dedup_body=f"<section class='card'><h2>Politique anti-perte</h2><p>Déduplication douce: on signale des groupes probables, mais on ne supprime rien. La fiche canonique peut servir à l’affichage futur, tout en conservant tous les liens sources.</p><p>Groupes détectés: <b>{dedup['groups_count']}</b>. À revoir humainement: <b>{review_count}</b>.</p></section><div class='grid'>{dedup_rows}</div>"
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
        # Drop stale heavy fields from previous intelligence builds before recomputing.
        it.pop("dedup_alternative_links", None)
        it.pop("photo_status", None)
        it["location_intelligence"]=infer_location(it)
        loc_intel = it.get("location_intelligence") or {}
        commune = loc_intel.get("commune_inferred")
        precise = loc_intel.get("precise_location_label")
        if commune and commune != "Non précisée":
            # Keep public filters/cards aligned with the latest inference. This
            # also clears stale city values from older intelligence passes.
            it["city"] = commune
        if precise and precise != "Non précisée":
            it["location"] = precise
            it["district"] = loc_intel.get("district_best") or commune or it.get("district")
        # Gallery/photo readiness is already represented by local_image_url/local_image_urls/image_urls.
        # Do not duplicate it into listings.json: the public payload has a strict mobile budget.
    dedup=build_dedup(items)
    opp=opportunity(items)
    write_outputs(payload, dedup, opp)
    print(json.dumps({"ok": True, "listings": len(items), "dedup_groups": dedup["groups_count"], "top_opportunity": opp["top"][:3], "outputs": ["locations.html", "dedup.html", "opportunity.html", "alertes_cours.html"]}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
