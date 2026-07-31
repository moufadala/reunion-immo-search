#!/usr/bin/env python3
"""Non-destructive product-data enrichment layer for the Réunion immo SQLite DB.

Creates/refreshes product-facing normalization and duplicate metadata without
changing the raw scraper table. The raw table remains the source of truth;
frontend exports and QA can join through rental_listings_product.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB = Path(os.environ.get("IMMO_DB_PATH", "/opt/data/data/reunion_watch.db"))

COMMUNES = [
    "Saint-Denis", "Sainte-Marie", "Sainte-Clotilde", "Le Tampon", "Saint-Pierre",
    "Saint-Paul", "La Possession", "Saint-Leu", "Saint-André", "Saint-Louis",
    "Le Port", "Sainte-Suzanne", "Saint-Benoît", "Saint-Joseph", "Bras-Panon",
    "Entre-Deux", "Les Avirons", "Etang-Salé", "La Plaine des Palmistes",
    "Saint-Gilles les Bains", "La Saline", "Petite Ile", "Cilaos", "Salazie",
    "Trois-Bassins", "Saint-Philippe",
]

POSTAL_TO_COMMUNE = {
    "97400": "Saint-Denis", "97490": "Saint-Denis", "97438": "Sainte-Marie",
    "97441": "Sainte-Suzanne", "97440": "Saint-André", "97470": "Saint-Benoît",
    "97412": "Bras-Panon", "97431": "La Plaine des Palmistes", "97410": "Saint-Pierre",
    "97430": "Le Tampon", "97418": "Le Tampon", "97450": "Saint-Louis",
    "97480": "Saint-Joseph", "97414": "Entre-Deux", "97425": "Les Avirons",
    "97427": "Etang-Salé", "97460": "Saint-Paul", "97434": "Saint-Paul",
    "97435": "Saint-Paul", "97422": "La Saline", "97436": "Saint-Leu",
    "97419": "La Possession", "97420": "Le Port",
}

ALIASES = {
    "st denis": "Saint-Denis", "saint denis": "Saint-Denis", "sainte clotilde": "Saint-Denis",
    "ste clotilde": "Saint-Denis", "moufia": "Saint-Denis", "la bretagne": "Saint-Denis",
    "montgaillard": "Saint-Denis", "la montagne": "Saint-Denis", "chaudron": "Saint-Denis",
    "ste marie": "Sainte-Marie", "sainte marie": "Sainte-Marie", "saint marie": "Sainte-Marie",
    "st pierre": "Saint-Pierre", "saint pierre": "Saint-Pierre",
    "tampon": "Le Tampon", "le tampon": "Le Tampon",
    "st paul": "Saint-Paul", "saint paul": "Saint-Paul", "la saline": "La Saline",
    "saint gilles les hauts": "Saint-Paul", "st gilles les hauts": "Saint-Paul", "saint gilles": "Saint-Paul",
    "bellepierre": "Saint-Denis",
    "st leu": "Saint-Leu", "saint leu": "Saint-Leu",
    "saint andre": "Saint-André", "st andre": "Saint-André",
    "saint benoit": "Saint-Benoît", "st benoit": "Saint-Benoît",
    "sainte suzanne": "Sainte-Suzanne", "ste suzanne": "Sainte-Suzanne",
    "sainte anne": "Saint-Benoît", "ste anne": "Saint-Benoît",
    "saint joseph": "Saint-Joseph", "st joseph": "Saint-Joseph",
    "etang sale": "Etang-Salé", "étang salé": "Etang-Salé",
    "entre deux": "Entre-Deux", "petite ile": "Petite Ile",
    "trois bassins": "Trois-Bassins", "les trois bassins": "Trois-Bassins",
    "saint philippe": "Saint-Philippe", "st philippe": "Saint-Philippe",
}

REGION_BY_COMMUNE = {
    "Nord": {"Saint-Denis", "Sainte-Marie", "Sainte-Clotilde", "Sainte-Suzanne"},
    "Est": {"Saint-André", "Saint-Benoît", "Bras-Panon", "La Plaine des Palmistes", "Salazie"},
    "Ouest": {"Saint-Paul", "La Possession", "Le Port", "Saint-Leu", "La Saline", "Saint-Gilles les Bains", "Trois-Bassins"},
    "Sud": {"Saint-Pierre", "Le Tampon", "Saint-Louis", "Saint-Joseph", "Etang-Salé", "Entre-Deux", "Les Avirons", "Petite Ile", "Cilaos", "Saint-Philippe"},
}

HARD_NON_RESIDENTIAL_RE = re.compile(
    r"\b(local\s+(?:commercial|professionnel|m[eé]dical)|locaux\s+commerciaux|bureau(?:x)?\b|box\b|parking\b|garage\b|garde\s*-?\s*meuble|terrain\b|entrep[oô]t)\b",
    re.I,
)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm(value: Any) -> str:
    s = clean(value).lower().replace("-", " ")
    return (
        s.replace("à", "a").replace("â", "a").replace("ä", "a")
        .replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
        .replace("î", "i").replace("ï", "i").replace("ô", "o").replace("ö", "o")
        .replace("ù", "u").replace("û", "u").replace("ü", "u").replace("ç", "c")
    )


def combined_text(row: dict[str, Any]) -> str:
    return norm(" ".join(clean(row.get(k)) for k in ["city", "district", "title", "description", "url", "canonical_url"]))


def location_text(row: dict[str, Any], keys: list[str]) -> str:
    """Localisation-only text for commune detection.

    Important: do not use the long source description for commune detection.
    Agency boilerplate contains CPI numbers and fee-schedule postal codes that
    look like real Réunion postcodes but describe the agency, not the listing.
    """
    return norm(" ".join(clean(row.get(k)) for k in keys))


def _has_token(text: str, token: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text))


def _has_postal(text: str, postal: str) -> bool:
    return bool(re.search(rf"(?<!\d){re.escape(postal)}(?!\d)", text))


def _commune_from_text(text: str, *, allow_postal: bool) -> str | None:
    # Prefer explicit names/aliases over postcodes: some source URLs contain
    # contradictory slugs like "le-tampon-97422". A commune name is stronger
    # evidence than a naked postal code in these feeds.
    for alias, commune in ALIASES.items():
        if _has_token(text, alias):
            return commune
    for commune in COMMUNES:
        if _has_token(text, norm(commune)):
            return commune
    if allow_postal:
        for postal, commune in POSTAL_TO_COMMUNE.items():
            if _has_postal(text, postal):
                return commune
    return None


def detect_commune(row: dict[str, Any]) -> str:
    # Source-of-truth hierarchy:
    # 1) district/portal structured locality when present;
    # 2) listing title, which often carries the advertised city;
    # 3) raw city only as fallback because some sources store the agency city;
    # 4) URL slug/postcode last. Never use long description: it contains agency
    # boilerplate, CPI numbers and fee-schedule postcodes unrelated to the good.
    for keys in (["district"], ["title"], ["city"], ["url", "canonical_url"]):
        text = location_text(row, keys)
        commune = _commune_from_text(text, allow_postal=True)
        if commune:
            return commune

    return clean(row.get("city")) or "Non précisée"


def detect_zone(row: dict[str, Any], commune: str) -> tuple[str, list[str]]:
    text = combined_text(row)
    zones: list[str] = []
    if "moufia" in text:
        zones.append("Moufia")
    if "la bretagne" in text or re.search(r"\bbretagne\b", text):
        zones.append("La Bretagne")
    if "sainte marie" in text or "saint marie" in text:
        zones.append("Sainte-Marie")
    if "saint denis" in text or "sainte clotilde" in text or "montgaillard" in text or "la montagne" in text:
        zones.append("Saint-Denis")
    order = ["Moufia", "La Bretagne", "Sainte-Marie", "Saint-Denis"]
    zones = [z for z in order if z in zones]
    if zones:
        return zones[0], zones
    if commune and commune != "Non précisée":
        return commune, [commune]
    return "Zone non précisée", []


def region_for(commune: str) -> str:
    for region, communes in REGION_BY_COMMUNE.items():
        if commune in communes:
            return region
    return "Région non précisée"


def property_label(row: dict[str, Any]) -> str:
    text = norm(f"{row.get('property_type') or ''} {row.get('title') or ''}")
    if any(x in text for x in ["maison", "villa", "house"]):
        return "Maison"
    if any(x in text for x in ["appartement", "studio", "flat", "apartment", "duplex", "t1", "t2", "t3", "t4", "t5"]):
        return "Appartement"
    return "Appartement / maison"


def residential_status(row: dict[str, Any]) -> tuple[str, list[str]]:
    title = clean(row.get("title"))
    desc = clean(row.get("description"))
    ptype = norm(row.get("property_type"))
    url = clean(row.get("url"))
    reasons: list[str] = []
    if ptype in {"commercial", "box"}:
        reasons.append(f"property_type={ptype}")
    m = HARD_NON_RESIDENTIAL_RE.search(" ".join([title, ptype, url]))
    if m:
        reasons.append(f"strong_keyword={m.group(1)}")
    dm = re.search(r"\b(local\s+(?:commercial|professionnel|m[eé]dical)|locaux\s+commerciaux)\b", desc, re.I)
    residential_hint = re.search(r"\b(maison|villa|appartement|studio|duplex|t[1-6]|f[1-6])\b", " ".join([title, ptype]), re.I)
    incidental_local = re.search(r"(possibilit[eé].{0,80}local\s+m[eé]dical|am[eé]nag[eé]e?.{0,80}cabinet\s+m[eé]dical)", desc, re.I)
    if dm and not (residential_hint and incidental_local):
        reasons.append(f"description_keyword={dm.group(1)}")
    if row.get("rent_eur") and row["rent_eur"] > 10000:
        reasons.append("rent_eur>10000")
    if row.get("source_site") == "seloger" and "/annonces/" in url and "-974/" not in url:
        reasons.append("outside_reunion_url")
    return ("suspect_non_residential" if reasons else "residential_candidate"), reasons


def dup_norm(s: Any) -> str:
    value = norm(s)
    for ch in "'_,.;:/()[]{}":
        value = value.replace(ch, " ")
    stop = {"appartement", "maison", "location", "louer", "pieces", "piece", "saint", "denis", "sainte", "marie", "reunion", "974"}
    toks = [t for t in value.split() if len(t) > 2 and t not in stop]
    return " ".join(toks[:6])


def duplicate_identity_token(row: dict[str, Any]) -> str | None:
    """Identity signal required before grouping duplicates.

    Numeric resemblance is not an identity proof on dense Réunion rentals. We use,
    in order, normalized landlord, significant title words, then exact non-round
    rent as a last-resort identity token. Rows without such a signal remain
    singletons and are not deduplicated by price+surface alone.
    """
    landlord = dup_norm(row.get("agency_or_owner"))
    landlord_stop = {"immobilier", "immo", "agence", "gestion", "transaction", "transactions", "sarl", "sas", "ei", "pro", "private", "particulier", "particuliers", "professionnel", "proprietaire", "bailleur"}
    landlord_tokens = [t for t in landlord.split() if t not in landlord_stop]
    if landlord_tokens:
        return "landlord:" + " ".join(landlord_tokens[:4])

    title = dup_norm(row.get("title"))
    title_stop = {"appartement", "maison", "villa", "location", "louer", "pieces", "piece", "saint", "denis", "sainte", "marie", "suzanne", "andre", "reunion", "974", "avec", "dans", "pour", "sur", "une", "des", "les", "proche"}
    title_tokens = [t for t in title.split() if len(t) >= 4 and t not in title_stop]
    if len(title_tokens) >= 2:
        return "title:" + " ".join(title_tokens[:6])

    rent = row.get("rent_eur")
    if rent and int(rent) % 50 != 0:
        return f"nonround-rent:{int(rent)}"
    return None


def duplicate_key(enriched: dict[str, Any], row: dict[str, Any]) -> str:
    ident = duplicate_identity_token(row)
    if not ident:
        return f"single|{row['source_site']}|{row['source_id']}"
    surface = round((row.get("surface_m2") or 0) / 5) * 5 if row.get("surface_m2") else 0
    # Exact non-round prices matter; otherwise use a broad bucket only *after*
    # the identity signal above is present.
    rent_value = row.get("rent_eur") or 0
    rent = int(rent_value) if rent_value and int(rent_value) % 50 != 0 else (round(rent_value / 50) * 50 if rent_value else 0)
    return "|".join(map(str, [
        enriched["city_normalized"], enriched["property_type_normalized"], surface, rent,
        row.get("rooms") or 0, ident[:80],
    ]))


def quality(row: dict[str, Any], enriched: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    score = 0
    missing: list[str] = []
    for field, label, points in [
        ("rent_eur", "loyer", 18), ("surface_m2", "surface", 18), ("rooms", "pièces", 10),
        ("bedrooms", "chambres", 8), ("url", "lien source", 10), ("image_url", "photo", 12),
        ("description", "description", 8), ("published_at", "date publication", 6),
    ]:
        value = row.get(field)
        if value is None or clean(value) == "":
            missing.append(label)
        else:
            score += points
    if enriched["city_normalized"] != "Non précisée":
        score += 16
    else:
        missing.append("ville normalisée")
    if enriched["region"] != "Région non précisée":
        score += 6
    flags = ["données clés présentes"] if not missing else ["données incomplètes: " + ", ".join(missing)]
    return min(score, 100), missing, flags


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS listing_product_enrichment (
          source_site TEXT NOT NULL,
          source_id TEXT NOT NULL,
          city_normalized TEXT NOT NULL,
          zone_normalized TEXT NOT NULL,
          zones_json TEXT NOT NULL,
          region TEXT NOT NULL,
          property_type_normalized TEXT NOT NULL,
          residential_status TEXT NOT NULL,
          residential_reasons_json TEXT NOT NULL,
          is_residential INTEGER NOT NULL,
          duplicate_key TEXT NOT NULL,
          duplicate_group_size INTEGER NOT NULL DEFAULT 1,
          duplicate_group_rank INTEGER NOT NULL DEFAULT 1,
          canonical_source_site TEXT,
          canonical_source_id TEXT,
          is_canonical INTEGER NOT NULL DEFAULT 1,
          quality_score INTEGER NOT NULL,
          missing_fields_json TEXT NOT NULL,
          trust_flags_json TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (source_site, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_lpe_city ON listing_product_enrichment(city_normalized);
        CREATE INDEX IF NOT EXISTS idx_lpe_region ON listing_product_enrichment(region);
        CREATE INDEX IF NOT EXISTS idx_lpe_residential ON listing_product_enrichment(is_residential, is_canonical);
        CREATE INDEX IF NOT EXISTS idx_lpe_duplicate ON listing_product_enrichment(duplicate_key, duplicate_group_rank);
        DROP VIEW IF EXISTS rental_listings_product;
        CREATE VIEW rental_listings_product AS
          SELECT r.*, e.city_normalized, e.zone_normalized, e.zones_json, e.region,
                 e.property_type_normalized, e.residential_status, e.residential_reasons_json,
                 e.is_residential, e.duplicate_key, e.duplicate_group_size, e.duplicate_group_rank,
                 e.canonical_source_site, e.canonical_source_id, e.is_canonical,
                 e.quality_score, e.missing_fields_json, e.trust_flags_json, e.updated_at AS enrichment_updated_at
          FROM rental_listings r
          LEFT JOIN listing_product_enrichment e
            ON e.source_site = r.source_site AND e.source_id = r.source_id;
        DROP VIEW IF EXISTS rental_listings_canonical_residential;
        CREATE VIEW rental_listings_canonical_residential AS
          SELECT * FROM rental_listings_product
          WHERE COALESCE(is_active,1)=1 AND is_residential=1 AND is_canonical=1;
        """
    )


def choose_canonical(group: list[tuple[dict[str, Any], dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, Any]]:
    def rank(pair: tuple[dict[str, Any], dict[str, Any]]) -> tuple[int, int, int, str]:
        row, enriched = pair
        # Prefer higher quality, source description, active/photographed, then stable id.
        has_desc = 1 if clean(row.get("description")) else 0
        has_photo = 1 if clean(row.get("image_url")) else 0
        return (enriched["quality_score"], has_desc, has_photo, f"{row['source_site']}:{row['source_id']}")

    return sorted(group, key=rank, reverse=True)[0]


def refresh(db: Path = DB) -> dict[str, Any]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    ensure_schema(con)
    rows = [dict(r) for r in con.execute("SELECT * FROM rental_listings WHERE COALESCE(is_active,1)=1")]
    now = datetime.now(timezone.utc).isoformat()

    staged: list[tuple[dict[str, Any], dict[str, Any]]] = []
    groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for row in rows:
        commune = detect_commune(row)
        zone, zones = detect_zone(row, commune)
        status, reasons = residential_status(row)
        enriched = {
            "source_site": row["source_site"],
            "source_id": row["source_id"],
            "city_normalized": commune,
            "zone_normalized": zone,
            "zones_json": json.dumps(zones, ensure_ascii=False),
            "region": region_for(commune),
            "property_type_normalized": property_label(row),
            "residential_status": status,
            "residential_reasons_json": json.dumps(reasons, ensure_ascii=False),
            "is_residential": 1 if status == "residential_candidate" else 0,
            "updated_at": now,
        }
        enriched["duplicate_key"] = duplicate_key(enriched, row)
        q, missing, flags = quality(row, enriched)
        enriched["quality_score"] = q
        enriched["missing_fields_json"] = json.dumps(missing, ensure_ascii=False)
        enriched["trust_flags_json"] = json.dumps(flags, ensure_ascii=False)
        staged.append((row, enriched))
        groups[enriched["duplicate_key"]].append((row, enriched))

    for group in groups.values():
        canonical_row, _ = choose_canonical(group)
        ordered = sorted(group, key=lambda pair: f"{pair[0]['source_site']}:{pair[0]['source_id']}")
        for rank, (row, enriched) in enumerate(ordered, 1):
            enriched["duplicate_group_size"] = len(group)
            enriched["duplicate_group_rank"] = rank
            enriched["canonical_source_site"] = canonical_row["source_site"]
            enriched["canonical_source_id"] = canonical_row["source_id"]
            enriched["is_canonical"] = 1 if (row["source_site"], row["source_id"]) == (canonical_row["source_site"], canonical_row["source_id"]) else 0

    columns = [
        "source_site", "source_id", "city_normalized", "zone_normalized", "zones_json", "region",
        "property_type_normalized", "residential_status", "residential_reasons_json", "is_residential",
        "duplicate_key", "duplicate_group_size", "duplicate_group_rank", "canonical_source_site",
        "canonical_source_id", "is_canonical", "quality_score", "missing_fields_json", "trust_flags_json", "updated_at",
    ]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{c}=excluded.{c}" for c in columns[2:])
    con.executemany(
        f"INSERT INTO listing_product_enrichment ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(source_site, source_id) DO UPDATE SET {updates}",
        [[enriched[c] for c in columns] for _, enriched in staged],
    )
    # Remove enrichment for rows no longer present OR no longer active in the raw table.
    # The product enrichment table is intentionally an active-listing cache; stale
    # raw rows remain preserved in rental_listings but must not leak into duplicate
    # groups, quality counts or product views after a successful source refresh.
    con.execute(
        """
        DELETE FROM listing_product_enrichment
        WHERE NOT EXISTS (
          SELECT 1 FROM rental_listings r
          WHERE r.source_site=listing_product_enrichment.source_site
            AND r.source_id=listing_product_enrichment.source_id
            AND COALESCE(r.is_active,1)=1
        )
        """
    )
    con.commit()

    total = len(rows)
    city_missing = sum(1 for _, e in staged if e["city_normalized"] == "Non précisée")
    duplicate_groups = sum(1 for group in groups.values() if len(group) > 1)
    duplicate_items = sum(len(group) for group in groups.values() if len(group) > 1)
    residential = sum(1 for _, e in staged if e["is_residential"] == 1)
    canonical_residential = sum(1 for _, e in staged if e["is_residential"] == 1 and e.get("is_canonical") == 1)
    return {
        "ok": True,
        "db": str(db),
        "active_rows": total,
        "city_normalized_missing": city_missing,
        "city_normalized_coverage_pct": round(100 * (total - city_missing) / max(total, 1), 1),
        "residential_candidates": residential,
        "canonical_residential": canonical_residential,
        "duplicate_groups": duplicate_groups,
        "duplicate_items": duplicate_items,
        "updated_at": now,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB)
    args = parser.parse_args()
    print(json.dumps(refresh(args.db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
