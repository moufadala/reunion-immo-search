#!/usr/bin/env python3
"""Phase B P0 ingest: JSON listings -> single writable SQLite WAL store.

Scope intentionally limited to Phase B:
- one SQLite database in WAL mode;
- native-ID deduplication with UNIQUE(source, site_id);
- no address-based deduplication;
- core listing fields commune/prix/surface;
- photo fingerprints stored as photos_phash/listing_photos.

The project environment currently has no Pillow/imagehash/ImageMagick, so photo
fingerprints are deterministic 16-hex content hashes for local cached image files
when available, falling back to URL hashes. The method is stored per photo so a
future real perceptual hash migration can distinguish them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "artifacts" / "app" / "listings.json"
DEFAULT_DB = ROOT / "data" / "socle_p0.sqlite"
HEX16_RE = re.compile(r"^[0-9a-fA-F]{16}$")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            site_id TEXT NOT NULL,
            external_id TEXT,
            title TEXT,
            url TEXT,
            commune TEXT,
            prix INTEGER,
            surface REAL,
            raw_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source, site_id)
        );
        CREATE INDEX IF NOT EXISTS idx_listings_source_site_id ON listings(source, site_id);
        CREATE INDEX IF NOT EXISTS idx_listings_commune_prix_surface ON listings(commune, prix, surface);

        CREATE TABLE IF NOT EXISTS listing_photos (
            listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
            phash TEXT NOT NULL,
            url TEXT,
            method TEXT NOT NULL DEFAULT 'sha256_16',
            created_at TEXT NOT NULL,
            PRIMARY KEY(listing_id, phash)
        );
        CREATE INDEX IF NOT EXISTS idx_listing_photos_phash ON listing_photos(phash);
        """
    )


def load_listings(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return [x for x in data.get("listings") or [] if isinstance(x, dict)]


def native_source(item: dict[str, Any]) -> str | None:
    value = item.get("source") or item.get("source_site")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def native_site_id(item: dict[str, Any], source: str | None) -> str | None:
    for key in ("source_id", "site_id"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    value = item.get("id")
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    prefix = f"{source}:" if source else ""
    if prefix and text.startswith(prefix) and len(text) > len(prefix):
        return text[len(prefix):]
    return text


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    text = str(value)
    digits = re.sub(r"[^0-9,.-]", "", text).replace(",", ".")
    if not digits or digits in {".", "-", "-."}:
        return None
    try:
        return int(round(float(digits)))
    except Exception:
        return None


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    digits = re.sub(r"[^0-9,.-]", "", text).replace(",", ".")
    if not digits or digits in {".", "-", "-."}:
        return None
    try:
        return float(digits)
    except Exception:
        return None


def commune(item: dict[str, Any]) -> str | None:
    value = item.get("commune") or item.get("city") or item.get("location")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def price(item: dict[str, Any]) -> int | None:
    return parse_int(item.get("prix", item.get("rent_eur", item.get("price"))))


def surface(item: dict[str, Any]) -> float | None:
    return parse_float(item.get("surface", item.get("surface_m2")))


def photo_urls(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("local_image_urls", "image_urls"):
        raw = item.get(key)
        if isinstance(raw, list):
            values.extend(str(x).strip() for x in raw if x)
    for key in ("local_image_url", "image_url"):
        raw = item.get(key)
        if raw:
            values.append(str(raw).strip())
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def provided_phashes(item: dict[str, Any]) -> list[str]:
    raw = item.get("photos_phash") or item.get("photo_phashes") or []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    if isinstance(raw, list):
        for value in raw:
            text = str(value).strip().lower()
            if HEX16_RE.match(text):
                out.append(text)
    return out


def fingerprint_for_url(url: str, app_root: Path) -> tuple[str, str]:
    candidate = app_root / url if not re.match(r"^[a-z]+:", url, flags=re.I) else None
    if candidate and candidate.exists() and candidate.is_file():
        return hashlib.sha256(candidate.read_bytes()).hexdigest()[:16], "sha256_16_file"
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16], "sha256_16_url"


def upsert_listing(con: sqlite3.Connection, item: dict[str, Any], now: str) -> tuple[int | None, str]:
    source = native_source(item)
    site_id = native_site_id(item, source)
    if not source or not site_id:
        return None, "skipped_missing_native_id"
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True)
    params = {
        "source": source,
        "site_id": site_id,
        "external_id": None if item.get("id") is None else str(item.get("id")),
        "title": item.get("title"),
        "url": item.get("url"),
        "commune": commune(item),
        "prix": price(item),
        "surface": surface(item),
        "raw_json": raw,
        "now": now,
    }
    existed = con.execute("SELECT id FROM listings WHERE source=? AND site_id=?", (source, site_id)).fetchone()
    con.execute(
        """
        INSERT INTO listings(source, site_id, external_id, title, url, commune, prix, surface, raw_json, first_seen_at, updated_at)
        VALUES (:source, :site_id, :external_id, :title, :url, :commune, :prix, :surface, :raw_json, :now, :now)
        ON CONFLICT(source, site_id) DO UPDATE SET
            external_id=excluded.external_id,
            title=excluded.title,
            url=excluded.url,
            commune=excluded.commune,
            prix=excluded.prix,
            surface=excluded.surface,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        params,
    )
    row = con.execute("SELECT id FROM listings WHERE source=? AND site_id=?", (source, site_id)).fetchone()
    return int(row["id"]), "updated" if existed else "inserted"


def ingest(source: Path = DEFAULT_SOURCE, db_path: Path = DEFAULT_DB, now: str | None = None) -> dict[str, Any]:
    timestamp = now or utcnow()
    listings = load_listings(source)
    con = connect(db_path)
    init_schema(con)
    counts = {"processed": 0, "inserted": 0, "updated": 0, "skipped_missing_native_id": 0, "photos_inserted": 0}
    app_root = source.parent
    with con:
        for item in listings:
            counts["processed"] += 1
            listing_id, status = upsert_listing(con, item, timestamp)
            counts[status] = counts.get(status, 0) + 1
            if listing_id is None:
                continue
            photo_pairs: list[tuple[str, str | None, str]] = []
            for phash in provided_phashes(item):
                photo_pairs.append((phash, None, "provided_phash"))
            for url in photo_urls(item):
                phash, method = fingerprint_for_url(url, app_root)
                photo_pairs.append((phash, url, method))
            for phash, url, method in photo_pairs:
                before = con.total_changes
                con.execute(
                    """INSERT OR IGNORE INTO listing_photos(listing_id, phash, url, method, created_at)
                    VALUES (?,?,?,?,?)""",
                    (listing_id, phash.lower(), url, method, timestamp),
                )
                if con.total_changes > before:
                    counts["photos_inserted"] += 1
    total_listings = int(con.execute("SELECT COUNT(*) FROM listings").fetchone()[0])
    total_photos = int(con.execute("SELECT COUNT(*) FROM listing_photos").fetchone()[0])
    journal_mode = str(con.execute("PRAGMA journal_mode").fetchone()[0])
    con.close()
    return {
        "ok": True,
        "source": str(source),
        "db": str(db_path),
        "journal_mode": journal_mode,
        "snapshot_at": timestamp,
        **counts,
        "total_listings": total_listings,
        "total_photos": total_photos,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase B P0 JSON -> SQLite WAL ingest")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--snapshot-at")
    args = ap.parse_args()
    report = ingest(Path(args.source), Path(args.db), args.snapshot_at)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
