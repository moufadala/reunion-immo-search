#!/usr/bin/env python3
"""Import SeLoger CDP multipage artifact into the immo SQLite DB.

The live SeLoger collector writes /opt/data/artifacts/realestate/seloger_multipage_results.json
but historically did not refresh rental_listings. This importer bridges that gap safely:
- requires a healthy artifact by default (>=10 listings and >=10 prices),
- preserves existing rich DB fields when the artifact lacks them,
- updates seen_last_at/content fields for seen listings,
- marks old SeLoger rows inactive only after a healthy full artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(os.environ.get("IMMO_DB_PATH", "/opt/data/data/reunion_watch.db"))
DEFAULT_ARTIFACT = Path("/opt/data/artifacts/realestate/seloger_multipage_results.json")
SQLITE_PARAM_SAFE_BATCH = 900

SCHEMA = """
CREATE TABLE IF NOT EXISTS rental_listings (
    source_site TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT,
    title TEXT,
    city TEXT,
    district TEXT,
    property_type TEXT,
    rooms INTEGER,
    bedrooms INTEGER,
    surface_m2 REAL,
    rent_eur INTEGER,
    charges_eur INTEGER,
    agency_or_owner TEXT,
    published_at TEXT,
    seen_first_at TEXT NOT NULL,
    seen_last_at TEXT NOT NULL,
    image_url TEXT,
    description TEXT,
    raw_json_path TEXT,
    content_hash TEXT,
    is_active INTEGER DEFAULT 1,
    PRIMARY KEY(source_site, source_id)
)
"""

COMMUNES = {
    "saint-denis": "Saint-Denis",
    "sainte-marie": "Sainte-Marie",
    "sainte-suzanne": "Sainte-Suzanne",
    "sainte-clotilde": "Sainte-Clotilde",
    "saint-pierre": "Saint-Pierre",
    "le-tampon": "Le Tampon",
    "saint-paul": "Saint-Paul",
    "saint-gilles-les-bains": "Saint-Paul",
    "saint-gilles-les-hauts": "Saint-Paul",
    "la-saline-les-bains": "Saint-Paul",
    "la-possession": "La Possession",
    "saint-leu": "Saint-Leu",
    "trois-bassins": "Trois-Bassins",
    "les-trois-bassins": "Trois-Bassins",
    "saint-andre": "Saint-André",
    "saint-benoit": "Saint-Benoît",
    "sainte-anne": "Saint-Benoît",
    "bras-panon": "Bras-Panon",
    "saint-louis": "Saint-Louis",
    "saint-joseph": "Saint-Joseph",
    "saint-philippe": "Saint-Philippe",
    "le-port": "Le Port",
    "petite-ile": "Petite-Île",
    "l-etang-sale": "Étang-Salé",
    "etang-sale": "Étang-Salé",
    "les-avirons": "Les Avirons",
    "entre-deux": "Entre-Deux",
    "la-plaine-des-palmistes": "Plaine-des-Palmistes",
    "cilaos": "Cilaos",
}


def init_db(con: sqlite3.Connection) -> None:
    con.execute(SCHEMA)
    con.execute("CREATE INDEX IF NOT EXISTS idx_rental_seen_last ON rental_listings(seen_last_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_rental_source_seen ON rental_listings(source_site, seen_last_at)")


def normalize_city(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    low = text.lower().replace("_", "-").replace(" ", "-")
    low = re.sub(r"[^a-z0-9\-]", "", low)
    for slug, city in COMMUNES.items():
        if low == slug or low.startswith(slug + "-") or slug in low:
            return city
    # Preserve already normalized city names from richer future artifacts.
    for city in set(COMMUNES.values()):
        if text.lower().replace("-", " ") == city.lower().replace("-", " "):
            return city
    return None


def infer_city(url: str | None, raw: dict[str, Any] | None = None) -> str | None:
    raw = raw or {}
    for key in ("ville", "city", "commune", "localisation", "location", "quartier"):
        city = normalize_city(raw.get(key))
        if city:
            return city
    if not url:
        return None
    low = url.lower()
    for slug, city in COMMUNES.items():
        if f"/{slug}-974" in low or f"/{slug}/" in low or f"/{slug}-" in low:
            return city
    return None


def normalize_item(a: dict[str, Any], artifact_path: Path) -> dict[str, Any]:
    sid = str(a.get("id") or "").strip()
    if not sid:
        raise ValueError("missing SeLoger id")
    url = a.get("url") or f"https://www.seloger.com/{sid}/detail.htm"
    city = infer_city(url, a)
    ptype = a.get("type_bien") or "Appartement / maison"
    rooms = a.get("nb_pieces")
    surface = a.get("surface")
    rent = a.get("prix")
    title_bits = [str(ptype)]
    if rooms:
        title_bits.append(f"{rooms} pièce(s)")
    if surface:
        title_bits.append(f"{surface:g} m²")
    if city:
        title_bits.append(city)
    title = "Location " + " · ".join(title_bits)
    desc_bits = ["Annonce SeLoger collectée par CDP"]
    if city:
        desc_bits.append(f"commune: {city}")
    if rent:
        desc_bits.append(f"loyer: {int(rent)} €")
    if surface:
        desc_bits.append(f"surface: {float(surface):g} m²")
    desc_bits.append(f"source: {url}")
    desc = ". ".join(desc_bits) + "."
    basis = {
        "id": sid,
        "url": url,
        "rent": rent,
        "surface": surface,
        "rooms": rooms,
        "bedrooms": a.get("nb_chambres"),
        "image_url": a.get("image_url"),
    }
    return {
        "source_site": "seloger",
        "source_id": sid,
        "url": url,
        "canonical_url": url,
        "title": title,
        "city": city,
        "district": None,
        "property_type": ptype,
        "rooms": int(rooms) if rooms not in (None, "") else None,
        "bedrooms": int(a["nb_chambres"]) if a.get("nb_chambres") not in (None, "") else None,
        "surface_m2": float(surface) if surface is not None else None,
        "rent_eur": int(rent) if rent is not None else None,
        "charges_eur": None,
        "agency_or_owner": "SeLoger",
        "published_at": None,
        "image_url": a.get("image_url"),
        "description": desc,
        "raw_json_path": str(artifact_path),
        "content_hash": hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
    }


def coalesce_new(new: Any, old: Any) -> Any:
    return new if new not in (None, "") else old


def _batches(values: tuple[str, ...], size: int):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def mark_seloger_inactive_not_seen(
    con: sqlite3.Connection,
    seen_ids: set[str],
    *,
    batch_size: int = SQLITE_PARAM_SAFE_BATCH,
) -> int:
    seen = tuple(seen_ids)
    if not seen:
        return 0

    if len(seen) <= batch_size:
        placeholders = ",".join("?" for _ in seen)
        cur = con.execute(
            f"UPDATE rental_listings SET is_active=0 WHERE source_site='seloger' AND source_id NOT IN ({placeholders})",
            seen,
        )
        return cur.rowcount if cur.rowcount is not None else 0

    con.execute(
        "CREATE TEMP TABLE IF NOT EXISTS tmp_seloger_seen_ids "
        "(source_id TEXT PRIMARY KEY) WITHOUT ROWID"
    )
    con.execute("DELETE FROM tmp_seloger_seen_ids")
    try:
        for batch in _batches(seen, batch_size):
            con.executemany(
                "INSERT OR IGNORE INTO tmp_seloger_seen_ids(source_id) VALUES (?)",
                ((sid,) for sid in batch),
            )
        cur = con.execute(
            """
            UPDATE rental_listings
            SET is_active=0
            WHERE source_site='seloger'
              AND NOT EXISTS (
                  SELECT 1
                  FROM tmp_seloger_seen_ids seen
                  WHERE seen.source_id = rental_listings.source_id
              )
            """
        )
        return cur.rowcount if cur.rowcount is not None else 0
    finally:
        con.execute("DELETE FROM tmp_seloger_seen_ids")


def import_artifact(db: Path, artifact: Path, *, mark_inactive: bool = True, min_total: int = 10, min_prices: int = 10, max_age_hours: float | None = None) -> dict[str, Any]:
    if max_age_hours is not None:
        age_hours = (time.time() - artifact.stat().st_mtime) / 3600
        if age_hours > max_age_hours:
            raise RuntimeError(f"Stale SeLoger artifact: age_hours={age_hours:.2f} max_age_hours={max_age_hours}")
    data = json.loads(artifact.read_text(encoding="utf-8"))
    annonces = data.get("annonces") or []
    with_price = sum(1 for a in annonces if a.get("prix"))
    if len(annonces) < min_total or with_price < min_prices:
        raise RuntimeError(f"Unhealthy SeLoger artifact: total={len(annonces)} with_price={with_price}")
    now = datetime.now(timezone.utc).isoformat()
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    init_db(con)
    inserted = updated = changed = 0
    seen_ids: set[str] = set()
    try:
        for raw in annonces:
            item = normalize_item(raw, artifact)
            sid = item["source_id"]
            seen_ids.add(sid)
            old = con.execute("SELECT * FROM rental_listings WHERE source_site='seloger' AND source_id=?", (sid,)).fetchone()
            if old is None:
                con.execute(
                    """INSERT INTO rental_listings (source_site,source_id,url,canonical_url,title,city,district,property_type,rooms,bedrooms,surface_m2,rent_eur,charges_eur,agency_or_owner,published_at,seen_first_at,seen_last_at,image_url,description,raw_json_path,content_hash,is_active)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (item["source_site"], item["source_id"], item["url"], item["canonical_url"], item["title"], item["city"], item["district"], item["property_type"], item["rooms"], item["bedrooms"], item["surface_m2"], item["rent_eur"], item["charges_eur"], item["agency_or_owner"], item["published_at"], now, now, item["image_url"], item["description"], item["raw_json_path"], item["content_hash"]),
                )
                inserted += 1
            else:
                if old["content_hash"] != item["content_hash"]:
                    changed += 1
                con.execute(
                    """UPDATE rental_listings SET url=?, canonical_url=?, title=?, city=?, district=?, property_type=?, rooms=?, bedrooms=?, surface_m2=?, rent_eur=?, charges_eur=?, agency_or_owner=?, published_at=?, seen_last_at=?, image_url=?, description=?, raw_json_path=?, content_hash=?, is_active=1 WHERE source_site='seloger' AND source_id=?""",
                    (
                        coalesce_new(item["url"], old["url"]), coalesce_new(item["canonical_url"], old["canonical_url"]), coalesce_new(item["title"], old["title"]),
                        coalesce_new(item["city"], old["city"]), coalesce_new(item["district"], old["district"]), coalesce_new(item["property_type"], old["property_type"]),
                        coalesce_new(item["rooms"], old["rooms"]), coalesce_new(item["bedrooms"], old["bedrooms"]), coalesce_new(item["surface_m2"], old["surface_m2"]),
                        coalesce_new(item["rent_eur"], old["rent_eur"]), coalesce_new(item["charges_eur"], old["charges_eur"]), coalesce_new(item["agency_or_owner"], old["agency_or_owner"]),
                        coalesce_new(item["published_at"], old["published_at"]), now, coalesce_new(item["image_url"], old["image_url"]), coalesce_new(item["description"], old["description"]),
                        str(artifact), item["content_hash"], sid,
                    ),
                )
                updated += 1
        inactive_marked = 0
        if mark_inactive:
            inactive_marked = mark_seloger_inactive_not_seen(con, seen_ids)
        con.commit()
    finally:
        con.close()
    return {
        "ok": True,
        "db": str(db),
        "artifact": str(artifact),
        "artifact_total": len(annonces),
        "artifact_with_price": with_price,
        "inserted": inserted,
        "updated": updated,
        "changed": changed,
        "inactive_marked": inactive_marked,
        "seen_ids": len(seen_ids),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    ap.add_argument("--no-mark-inactive", action="store_true")
    ap.add_argument("--max-age-hours", type=float, default=None, help="Reject artifact if its mtime is older than this many hours.")
    args = ap.parse_args()
    result = import_artifact(args.db, args.artifact, mark_inactive=not args.no_mark_inactive, max_age_hours=args.max_age_hours)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
