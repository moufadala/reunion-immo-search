#!/usr/bin/env python3
"""Bien'ici rentals with explicit, machine-verifiable acquisition evidence.

The public JSON endpoint exposes an island-wide total.  Collection continues
until that total is reached (or an explicit empty terminal page is observed
when no total is exposed).  A page cap, malformed response, or request error is
always reported as partial/failed and is never authoritative for withdrawals.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import os
import json
import re
import sqlite3
import ssl
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://www.bienici.com/realEstateAds.json"
REFERER = "https://www.bienici.com/recherche/location/la-reunion-974/appartement"
DEFAULT_DB = Path("/opt/data/data/reunion_watch.db")
DEFAULT_ARTIFACT_DIR = Path("/opt/data/artifacts/realestate/bienici")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/125 Safari/537.36 HermesPersonalWatcher/1.0"
)

# Saint-Denis (including Sainte-Clotilde / La Montagne postal sectors) and
# Sainte-Marie only.  No East-island postal code belongs in this set.
TARGET_POSTAL_CODES = {"97400", "97490", "97417", "97495", "97438"}
RESIDENTIAL_PROPERTY_TYPES = {"flat", "house", "apartment", "maison"}
IDENTITY_REDUCTION_REASONS = {"duplicate_id", "missing_id"}


@dataclass
class Listing:
    source_site: str
    source_id: str
    url: str
    canonical_url: str
    title: str | None
    city: str | None
    district: str | None
    property_type: str | None
    rooms: int | None
    bedrooms: int | None
    surface_m2: float | None
    rent_eur: int | None
    charges_eur: int | None
    agency_or_owner: str | None
    published_at: str | None
    image_url: str | None
    description: str | None
    raw_json_path: str | None
    content_hash: str


@dataclass
class CollectionResult:
    ads: list[dict[str, Any]] = field(default_factory=list)
    page_payloads: list[dict[str, Any]] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    page_sizes: list[int] = field(default_factory=list)
    reported_totals: list[int] = field(default_factory=list)
    reported_total: int | None = None
    pages_attempted: int = 0
    pages_succeeded: int = 0
    complete: bool = False
    terminal_reason: str = "not_started"
    truncation_signals: list[str] = field(default_factory=list)
    error: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_json(url: str) -> dict[str, Any]:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Referer": REFERER,
        },
    )
    with urlopen(req, timeout=40, context=ssl.create_default_context()) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def build_url(
    page: int = 1,
    size: int = 50,
    property_types: list[str] | None = None,
    max_price: int | None = None,
    min_rooms: int | None = None,
    cities_zone_id: str = "-77601",
) -> str:
    filters: dict[str, Any] = {
        "size": size,
        "from": (page - 1) * size,
        "filterType": "rent",
        "propertyType": property_types or ["flat", "house"],
        "page": page,
        "sortBy": "relevance",
        "sortOrder": "desc",
        "onTheMarket": [True],
        "zoneIdsByTypes": {"zoneIds": [cities_zone_id]},
    }
    if max_price is not None:
        filters["maxPrice"] = max_price
    if min_rooms is not None:
        filters["minRooms"] = min_rooms
    query = urlencode(
        {
            "filters": json.dumps(filters, ensure_ascii=False, separators=(",", ":")),
            "extensionType": "extendedIfNoResult",
            "enableGoogleStructuredDataAggregates": "true",
            "leadingCount": "2",
        }
    )
    return f"{BASE_URL}?{query}"


def _reported_total(payload: dict[str, Any]) -> int | None:
    value = payload.get("total")
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("total must be a non-negative integer")
    parsed = int(value)
    if parsed < 0:
        raise ValueError("total must be a non-negative integer")
    return parsed


def collect_pages(
    fetch: Callable[[str], dict[str, Any]],
    *,
    size: int = 50,
    max_pages: int = 100,
    property_types: list[str] | None = None,
    max_price: int | None = None,
    min_rooms: int | None = None,
) -> CollectionResult:
    if size <= 0 or max_pages <= 0:
        raise ValueError("size and max_pages must be positive")
    result = CollectionResult()

    for page in range(1, max_pages + 1):
        url = build_url(
            page=page,
            size=size,
            property_types=property_types,
            max_price=max_price,
            min_rooms=min_rooms,
        )
        result.urls.append(url)
        result.pages_attempted += 1
        try:
            payload = fetch(url)
            if not isinstance(payload, dict):
                raise ValueError("response payload is not an object")
            ads = payload.get("realEstateAds")
            if ads is None:
                ads = []
            if not isinstance(ads, list):
                raise ValueError("realEstateAds is not an array")
            page_total = _reported_total(payload)
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.terminal_reason = "page_error"
            result.truncation_signals.append(f"page_error:{page}")
            break

        result.pages_succeeded += 1
        result.page_payloads.append(payload)
        result.page_sizes.append(len(ads))
        result.ads.extend(ad for ad in ads if isinstance(ad, dict))
        if len(result.ads) != sum(result.page_sizes):
            result.error = "one or more API items were not objects"
            result.terminal_reason = "page_error"
            result.truncation_signals.append(f"malformed_items:{page}")
            break

        if page_total is not None:
            result.reported_totals.append(page_total)
            result.reported_total = max(result.reported_totals)
            if len(result.ads) >= result.reported_total:
                result.complete = True
                result.terminal_reason = "reported_total_reached"
                break

        if not ads:
            if result.reported_total is not None and len(result.ads) < result.reported_total:
                result.error = (
                    f"empty page {page} before reported total "
                    f"{result.reported_total} (fetched {len(result.ads)})"
                )
                result.terminal_reason = "empty_before_reported_total"
                result.truncation_signals.append("empty_before_reported_total")
            else:
                result.complete = True
                result.reported_total = len(result.ads)
                result.terminal_reason = "empty_page"
            break
    else:
        result.terminal_reason = "page_cap"
        result.truncation_signals.append(f"page_cap:{max_pages}")

    if not result.complete and result.terminal_reason == "not_started":
        result.terminal_reason = "page_cap"
        result.truncation_signals.append(f"page_cap:{max_pages}")
    return result


def clean(value: Any) -> str | None:
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def classify_ad(ad: dict[str, Any]) -> str | None:
    if not str(ad.get("id") or "").strip():
        return "missing_id"
    if str(ad.get("postalCode") or "").strip() not in TARGET_POSTAL_CODES:
        return "postal_scope"
    property_type = str(ad.get("propertyType") or "").strip().lower()
    if property_type not in RESIDENTIAL_PROPERTY_TYPES:
        return "commercial_or_nonresidential"
    return None


def select_target_ads(
    ads: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: list[dict[str, Any]] = []
    rejections: Counter[str] = Counter()
    seen: set[str] = set()
    for ad in ads:
        source_id = str(ad.get("id") or "").strip()
        if source_id and source_id in seen:
            rejections["duplicate_id"] += 1
            continue
        if source_id:
            seen.add(source_id)
        reason = classify_ad(ad)
        if reason:
            rejections[reason] += 1
            continue
        selected.append(ad)
    return selected, dict(sorted(rejections.items()))


def ad_url(ad: dict[str, Any]) -> str:
    value = str(ad.get("url") or "").strip()
    if value:
        return f"https://www.bienici.com{value}" if value.startswith("/") else value
    return f"https://www.bienici.com/annonce/{ad.get('id')}"


def normalize(ad: dict[str, Any], *, raw_dir: Path) -> Listing:
    source_id = str(ad["id"])
    title = clean(ad.get("title")) or clean(str(ad.get("description") or "")[:120])
    url = ad_url(ad)
    district = ad.get("district")
    if isinstance(district, dict):
        district = district.get("name") or district.get("label")
    elif district is not None:
        district = str(district)
    photos = ad.get("photos") or []
    image = None
    if isinstance(photos, list) and photos and isinstance(photos[0], dict):
        image = photos[0].get("url") or photos[0].get("url_photo")
    price = ad.get("price") if ad.get("price") is not None else ad.get("rent")
    if isinstance(price, float):
        price = int(round(price))
    charges = ad.get("charges")
    if isinstance(charges, float):
        charges = int(round(charges))
    published = ad.get("publicationDate") or ad.get("modificationDate")
    if published == "1970-01-01T00:00:00.000Z":
        published = ad.get("modificationDate")
    content_basis = json.dumps(
        {
            "id": source_id,
            "price": price,
            "charges": charges,
            "modificationDate": ad.get("modificationDate"),
            "surfaceArea": ad.get("surfaceArea"),
            "description": (clean(ad.get("description")) or "")[:500],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"bienici_{source_id}.json"
    raw_path.write_text(json.dumps(ad, ensure_ascii=False, indent=2), encoding="utf-8")
    return Listing(
        source_site="bienici",
        source_id=source_id,
        url=url,
        canonical_url=url,
        title=title,
        city=ad.get("city"),
        district=district,
        property_type=ad.get("propertyType"),
        rooms=ad.get("roomsQuantity"),
        bedrooms=ad.get("bedroomsQuantity"),
        surface_m2=ad.get("surfaceArea"),
        rent_eur=price,
        charges_eur=charges,
        agency_or_owner=ad.get("accountDisplayName") or ad.get("accountType"),
        published_at=published,
        image_url=image,
        description=clean(ad.get("description")),
        raw_json_path=str(raw_path),
        content_hash=hashlib.sha256(content_basis.encode("utf-8")).hexdigest(),
    )


def init_db(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS rental_listings (
        source_site TEXT NOT NULL, source_id TEXT NOT NULL, url TEXT NOT NULL,
        canonical_url TEXT, title TEXT, city TEXT, district TEXT,
        property_type TEXT, rooms INTEGER, bedrooms INTEGER, surface_m2 REAL,
        rent_eur INTEGER, charges_eur INTEGER, agency_or_owner TEXT,
        published_at TEXT, seen_first_at TEXT NOT NULL, seen_last_at TEXT NOT NULL,
        image_url TEXT, description TEXT, raw_json_path TEXT, content_hash TEXT,
        is_active INTEGER DEFAULT 1, PRIMARY KEY(source_site, source_id))"""
    )


def ensure_listing_detail_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS listing_detail (
        source_site TEXT NOT NULL, source_id TEXT NOT NULL, fetched_at TEXT NOT NULL,
        http_status INTEGER, address TEXT, street TEXT, residence TEXT,
        postal_code TEXT, locality TEXT, lat REAL, lon REAL, precision TEXT,
        geo_source TEXT, floor TEXT, has_elevator INTEGER, bathtub INTEGER,
        furnished INTEGER, charges_eur INTEGER, bedrooms INTEGER,
        description_full TEXT, notes TEXT,
        PRIMARY KEY (source_site, source_id))"""
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(listing_detail)")}
    for name in ("jardin", "terrasse"):
        if name not in columns:
            connection.execute(f"ALTER TABLE listing_detail ADD COLUMN {name} INTEGER")


def upsert_confort(connection: sqlite3.Connection, ad: dict[str, Any]) -> None:
    values = (ad.get("isFurnished"), ad.get("hasGarden"), ad.get("hasTerrace"))
    if all(value is None for value in values):
        return
    encoded = [1 if value else (0 if value is not None else None) for value in values]
    connection.execute(
        """INSERT INTO listing_detail
        (source_site, source_id, fetched_at, http_status, furnished, jardin, terrasse)
        VALUES ('bienici',?,?,200,?,?,?)
        ON CONFLICT(source_site, source_id) DO UPDATE SET
        fetched_at=excluded.fetched_at, http_status=200,
        furnished=excluded.furnished, jardin=excluded.jardin,
        terrasse=excluded.terrasse""",
        (str(ad["id"]), utc_now(), *encoded),
    )


def upsert(connection: sqlite3.Connection, listing: Listing) -> str:
    now = utc_now()
    existing = connection.execute(
        "SELECT content_hash,is_active FROM rental_listings WHERE source_site=? AND source_id=?",
        (listing.source_site, listing.source_id),
    ).fetchone()
    values = asdict(listing)
    if existing is None:
        connection.execute(
            """INSERT INTO rental_listings
            (source_site,source_id,url,canonical_url,title,city,district,
             property_type,rooms,bedrooms,surface_m2,rent_eur,charges_eur,
             agency_or_owner,published_at,seen_first_at,seen_last_at,image_url,
             description,raw_json_path,content_hash,is_active)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (
                values["source_site"], values["source_id"], values["url"],
                values["canonical_url"], values["title"], values["city"],
                values["district"], values["property_type"], values["rooms"],
                values["bedrooms"], values["surface_m2"], values["rent_eur"],
                values["charges_eur"], values["agency_or_owner"],
                values["published_at"], now, now, values["image_url"],
                values["description"], values["raw_json_path"], values["content_hash"],
            ),
        )
        return "new"
    was_active = existing[1] is None or int(existing[1]) == 1
    status = (
        "reappeared" if not was_active
        else "changed" if existing[0] != listing.content_hash else "seen"
    )
    connection.execute(
        """UPDATE rental_listings SET url=?,canonical_url=?,title=?,city=?,
        district=?,property_type=?,rooms=?,bedrooms=?,surface_m2=?,rent_eur=?,
        charges_eur=?,agency_or_owner=?,published_at=?,seen_last_at=?,image_url=?,
        description=?,raw_json_path=?,content_hash=?,is_active=1
        WHERE source_site=? AND source_id=?""",
        (
            values["url"], values["canonical_url"], values["title"], values["city"],
            values["district"], values["property_type"], values["rooms"],
            values["bedrooms"], values["surface_m2"], values["rent_eur"],
            values["charges_eur"], values["agency_or_owner"], values["published_at"],
            now, values["image_url"], values["description"], values["raw_json_path"],
            values["content_hash"], values["source_site"], values["source_id"],
        ),
    )
    return status


def write_collection_artifact(result: CollectionResult, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "source": "bienici",
        "pages_attempted": result.pages_attempted,
        "pages_succeeded": result.pages_succeeded,
        "page_sizes": result.page_sizes,
        "reported_totals": result.reported_totals,
        "reported_total": result.reported_total,
        "complete": result.complete,
        "terminal_reason": result.terminal_reason,
        "truncation_signals": result.truncation_signals,
        "error": result.error,
        "urls": result.urls,
        "page_payloads": result.page_payloads,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_if_present(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(
    result: CollectionResult,
    *,
    run_id: str,
    event_statuses: list[str],
    normalized_ids: set[str],
    rejection_counts: dict[str, int],
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    artifact_sha = _sha256_if_present(artifact_path)
    status = "complete" if result.complete else ("failed" if result.pages_succeeded == 0 else "partial")
    signals = list(result.truncation_signals)
    terminal_reason = result.terminal_reason
    error = result.error
    if status == "failed" and not error:
        error = "collection failed before any page was proven complete"
    if status == "complete" and artifact_sha is None:
        status = "partial"
        terminal_reason = "artifact_missing"
        signals.append("artifact_missing")

    inserted = sum(value in {"new", "inserted"} for value in event_statuses)
    updated = sum(value in {"changed", "updated", "reappeared"} for value in event_statuses)
    reappeared = sum(value == "reappeared" for value in event_statuses)
    if inserted + updated > len(normalized_ids):
        raise ValueError("event statuses exceed normalized items")
    unchanged = len(normalized_ids) - inserted - updated
    post_unique_rejections = {
        str(reason): int(count) for reason, count in rejection_counts.items()
        if reason not in IDENTITY_REDUCTION_REASONS and int(count) > 0
    }
    pre_unique_rejections = {
        str(reason): int(count) for reason, count in rejection_counts.items()
        if reason in IDENTITY_REDUCTION_REASONS and int(count) > 0
    }
    normalized_rejections = sum(post_unique_rejections.values())
    unique_ids = len(normalized_ids) + normalized_rejections
    identity_gap = len(result.ads) - unique_ids
    if sum(pre_unique_rejections.values()) != identity_gap:
        pre_unique_rejections = {"identity_reduction": identity_gap} if identity_gap else {}

    if status == "complete" and result.reported_total is not None and unique_ids != result.reported_total:
        status = "partial"
        terminal_reason = "unique_id_gap"
        signals.append(
            f"unique_id_gap:{unique_ids}/{result.reported_total}"
        )

    return {
        "run_id": run_id,
        "source": "bienici",
        "status": status,
        "attempted": result.pages_attempted > 0,
        "pages_attempted": result.pages_attempted,
        "pages_succeeded": result.pages_succeeded,
        "fetched_items": len(result.ads),
        "parsed_items": len(result.ads),
        "unique_ids": unique_ids,
        "normalized_items": len(normalized_ids),
        "rejected_items": normalized_rejections,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "withdrawn": 0,
        "reappeared": reappeared,
        "expected_count": result.reported_total,
        "dataset_id": None,
        "retries": 0,
        "truncation_signals": signals,
        "error": error,
        "terminal_reason": terminal_reason,
        "page_sizes": result.page_sizes,
        "reported_totals": result.reported_totals,
        "rejected_items_by_reason": dict(sorted(post_unique_rejections.items())),
        "pre_unique_rejections_by_reason": dict(sorted(pre_unique_rejections.items())),
        "unparsed_items_by_reason": {},
        "seen_ids": sorted(str(value) for value in normalized_ids),
        "duplicate_items": int(rejection_counts.get("duplicate_id", 0)),
        "unidentified_items": int(rejection_counts.get("missing_id", 0)),
        "artifact_path": str(artifact_path) if artifact_path else None,
        "artifact_sha256": artifact_sha,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--size", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-price", type=int)
    parser.add_argument("--min-rooms", type=int)
    args = parser.parse_args(argv)
    run_id = args.run_id or os.environ.get("IMMO_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    result = collect_pages(
        fetch_json,
        size=args.size,
        max_pages=args.max_pages,
        property_types=["flat", "house"],
        max_price=args.max_price,
        min_rooms=args.min_rooms,
    )
    artifact_path = args.artifact_dir / f"collection-{run_id}.json"
    write_collection_artifact(result, artifact_path)
    selected_ads, rejection_counts = select_target_ads(result.ads)
    listings = [
        normalize(ad, raw_dir=args.artifact_dir / "raw")
        for ad in selected_ads
    ]

    events: list[str] = []
    if args.dry_run:
        events = ["seen"] * len(listings)
    elif listings:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(args.db)
        try:
            init_db(connection)
            ensure_listing_detail_table(connection)
            for listing in listings:
                events.append(upsert(connection, listing))
            for ad in selected_ads:
                upsert_confort(connection, ad)
            connection.commit()
        finally:
            connection.close()

    manifest = build_manifest(
        result,
        run_id=run_id,
        event_statuses=events,
        normalized_ids={listing.source_id for listing in listings},
        rejection_counts=rejection_counts,
        artifact_path=artifact_path,
    )
    manifest_path = args.manifest or args.artifact_dir / f"manifest-{run_id}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
