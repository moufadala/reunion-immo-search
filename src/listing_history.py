#!/usr/bin/env python3
"""Track listing history and price/status events from the generated immo JSON export.

Non-destructive by design: reads artifacts/app/listings.json and writes a small
auxiliary SQLite DB under /opt/data/artifacts/immo-alerts/history.sqlite.

Normal stdout is JSON summary for manual/QA usage. Cron wrappers should redirect
or suppress it unless a user-facing digest is desired.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "artifacts" / "app" / "listings.json"
DEFAULT_DB = Path("/opt/data/artifacts/immo-alerts/history.sqlite")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_listings(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("listings") or [])


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS listing_current (
            id TEXT PRIMARY KEY,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_snapshot_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            title TEXT,
            url TEXT,
            source_site TEXT,
            region TEXT,
            commune TEXT,
            rent_eur INTEGER,
            surface_m2 REAL,
            rooms INTEGER,
            bedrooms INTEGER,
            score INTEGER,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS listing_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_at TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            details_json TEXT,
            UNIQUE(listing_id, event_type, event_at, old_value, new_value)
        );
        CREATE INDEX IF NOT EXISTS idx_listing_events_type_at ON listing_events(event_type, event_at);
        CREATE INDEX IF NOT EXISTS idx_listing_current_active ON listing_current(active, last_seen_at);
        """
    )


def as_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(round(float(v)))
    except Exception:
        return None


def as_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def item_price(item: dict[str, Any]) -> int | None:
    return as_int(item.get("rent_eur", item.get("price")))


def item_surface(item: dict[str, Any]) -> float | None:
    return as_float(item.get("surface_m2", item.get("surface")))


def item_source(item: dict[str, Any]) -> str | None:
    return item.get("source_site") or item.get("source")


def item_commune(item: dict[str, Any]) -> str | None:
    return item.get("commune") or item.get("city")


def event(con: sqlite3.Connection, listing_id: str, typ: str, now: str, old: Any = None, new: Any = None, details: dict[str, Any] | None = None) -> None:
    con.execute(
        "INSERT INTO listing_events(listing_id,event_type,event_at,old_value,new_value,details_json) VALUES (?,?,?,?,?,?)",
        (listing_id, typ, now, None if old is None else str(old), None if new is None else str(new), json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
    )


def snapshot(source: Path = DEFAULT_SOURCE, db_path: Path = DEFAULT_DB, snapshot_at: str | None = None) -> dict[str, Any]:
    now = snapshot_at or utcnow()
    listings = load_listings(source)
    con = connect(db_path)
    init_schema(con)
    seen_ids = {str(x.get("id")) for x in listings if x.get("id")}
    counts = {"new": 0, "price_changed": 0, "reappeared": 0, "disappeared": 0, "unchanged": 0, "processed": 0}
    price_changes: list[dict[str, Any]] = []

    with con:
        for item in listings:
            lid = str(item.get("id") or "").strip()
            if not lid:
                continue
            counts["processed"] += 1
            rent = item_price(item)
            current = con.execute("SELECT * FROM listing_current WHERE id=?", (lid,)).fetchone()
            raw = json.dumps(item, ensure_ascii=False, sort_keys=True)
            params = (
                lid, now, now, now, 1, item.get("title"), item.get("url"), item_source(item),
                item.get("region"), item_commune(item), rent, item_surface(item), item.get("rooms"),
                item.get("bedrooms"), item.get("score"), raw,
            )
            if current is None:
                con.execute(
                    """INSERT INTO listing_current
                    (id,first_seen_at,last_seen_at,last_snapshot_at,active,title,url,source_site,region,commune,rent_eur,surface_m2,rooms,bedrooms,score,raw_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    params,
                )
                event(con, lid, "new", now, None, rent, {"title": item.get("title"), "url": item.get("url")})
                counts["new"] += 1
                continue

            if int(current["active"] or 0) == 0:
                event(con, lid, "reappeared", now, 0, 1, {"title": item.get("title"), "url": item.get("url")})
                counts["reappeared"] += 1
            old_rent = current["rent_eur"]
            if old_rent is not None and rent is not None and int(old_rent) != int(rent):
                event(con, lid, "price_changed", now, old_rent, rent, {"delta_eur": int(rent) - int(old_rent), "title": item.get("title"), "url": item.get("url")})
                counts["price_changed"] += 1
                price_changes.append({"id": lid, "old": int(old_rent), "new": int(rent), "delta": int(rent) - int(old_rent), "title": item.get("title")})
            else:
                counts["unchanged"] += 1
            con.execute(
                """UPDATE listing_current SET
                last_seen_at=?, last_snapshot_at=?, active=1, title=?, url=?, source_site=?, region=?, commune=?, rent_eur=?, surface_m2=?, rooms=?, bedrooms=?, score=?, raw_json=?
                WHERE id=?""",
                (now, now, item.get("title"), item.get("url"), item_source(item), item.get("region"), item_commune(item), rent, item_surface(item), item.get("rooms"), item.get("bedrooms"), item.get("score"), raw, lid),
            )

        # Mark previously active listings that disappeared from the current export.
        for row in con.execute("SELECT id,title,url,rent_eur FROM listing_current WHERE active=1").fetchall():
            if row["id"] not in seen_ids:
                con.execute("UPDATE listing_current SET active=0,last_snapshot_at=? WHERE id=?", (now, row["id"]))
                event(con, row["id"], "disappeared", now, 1, 0, {"title": row["title"], "url": row["url"], "last_rent_eur": row["rent_eur"]})
                counts["disappeared"] += 1

    totals = dict(con.execute("SELECT active, COUNT(*) c FROM listing_current GROUP BY active").fetchall())
    event_counts = dict(con.execute("SELECT event_type, COUNT(*) c FROM listing_events GROUP BY event_type").fetchall())
    con.close()
    return {
        "ok": True,
        "source": str(source),
        "db": str(db_path),
        "snapshot_at": now,
        "counts": counts,
        "active_current": int(totals.get(1, 0)),
        "inactive_current": int(totals.get(0, 0)),
        "event_counts": {str(k): int(v) for k, v in event_counts.items()},
        "price_changes": price_changes[:20],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--snapshot-at")
    args = ap.parse_args()
    report = snapshot(Path(args.source), Path(args.db), args.snapshot_at)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
