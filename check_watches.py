#!/usr/bin/env python3
"""Phase C P0: check saved watches and emit exactly-once alerts.

Scope intentionally limited to Phase C:
- adds `watches` and `watch_notifications` tables to the Phase B SQLite DB;
- matches active watches against current `listings` rows;
- emits one alert per new (watch, listing), never re-notifies the same pair.

Telegram is used only with --live. Tests can use --transport-log to avoid real sends.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "socle_p0.sqlite"
DEFAULT_ENV = Path("/opt/data/.env")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env(path: Path = DEFAULT_ENV) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            criteria_json TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_checked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS watch_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
            listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
            notified_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            message_hash TEXT NOT NULL,
            error TEXT,
            UNIQUE(watch_id, listing_id)
        );
        CREATE INDEX IF NOT EXISTS idx_watch_notifications_watch ON watch_notifications(watch_id, notified_at);
        CREATE INDEX IF NOT EXISTS idx_watches_active ON watches(active);
        """
    )


def normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def upsert_watch(con: sqlite3.Connection, name: str, criteria: dict[str, Any], now: str) -> int:
    criteria_text = json.dumps(criteria, ensure_ascii=False, sort_keys=True)
    con.execute(
        """
        INSERT INTO watches(name, criteria_json, active, created_at, updated_at)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            criteria_json=excluded.criteria_json,
            active=1,
            updated_at=excluded.updated_at
        """,
        (name, criteria_text, now, now),
    )
    row = con.execute("SELECT id FROM watches WHERE name=?", (name,)).fetchone()
    return int(row["id"])


def list_watches(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(con.execute("SELECT * FROM watches WHERE active=1 ORDER BY id"))


def as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(round(float(str(value).replace("€", "").replace(" ", ""))))
    except Exception:
        return None


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("m²", "").replace("m2", "").replace(" ", "").replace(",", "."))
    except Exception:
        return None


def row_matches(row: sqlite3.Row, criteria: dict[str, Any]) -> bool:
    commune = criteria.get("commune")
    if commune and normalize_text(row["commune"]) != normalize_text(commune):
        return False
    source = criteria.get("source")
    if source and normalize_text(row["source"]) != normalize_text(source):
        return False
    site_id = criteria.get("site_id")
    if site_id and str(row["site_id"]) != str(site_id):
        return False
    q = criteria.get("q")
    if q:
        hay = normalize_text(" ".join(str(row[k] or "") for k in ("title", "url", "commune", "source", "site_id")))
        if normalize_text(q) not in hay:
            return False
    prix = as_int(row["prix"])
    surface = as_float(row["surface"])
    if criteria.get("prix_min") is not None and (prix is None or prix < int(criteria["prix_min"])):
        return False
    if criteria.get("prix_max") is not None and (prix is None or prix > int(criteria["prix_max"])):
        return False
    if criteria.get("surface_min") is not None and (surface is None or surface < float(criteria["surface_min"])):
        return False
    if criteria.get("surface_max") is not None and (surface is None or surface > float(criteria["surface_max"])):
        return False
    return True


def matching_listings(con: sqlite3.Connection, criteria: dict[str, Any]) -> list[sqlite3.Row]:
    rows = list(con.execute("SELECT id, source, site_id, title, url, commune, prix, surface FROM listings ORDER BY id"))
    return [r for r in rows if row_matches(r, criteria)]


def alert_limit(criteria: dict[str, Any]) -> int:
    return max(1, min(int(criteria.get("limit") or 20), 50))


def alert_text(watch: sqlite3.Row, row: sqlite3.Row) -> str:
    title = row["title"] or "Annonce sans titre"
    parts = [
        "🏠 Phase C veille immo — nouvelle annonce",
        f"Watch: {watch['name']}",
        f"{title}",
        f"{row['commune'] or 'Commune n.c.'} · {row['prix'] or 'prix n.c.'} € · {row['surface'] or 'surface n.c.'} m²",
        f"Source: {row['source']} / {row['site_id']}",
    ]
    if row["url"]:
        parts.append(str(row["url"]))
    return "\n".join(parts)


def send_transport(text: str, *, live: bool, transport_log: Path | None) -> None:
    if transport_log is not None:
        transport_log.parent.mkdir(parents=True, exist_ok=True)
        with transport_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"sent_at": utcnow(), "text": text}, ensure_ascii=False) + "\n")
        return
    if not live:
        return
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_HOME_CHANNEL")
    if not token or not chat_id:
        raise RuntimeError("missing TELEGRAM_BOT_TOKEN or TELEGRAM_HOME_CHANNEL")
    params = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    thread_id = env.get("TELEGRAM_HOME_CHANNEL_THREAD_ID")
    if thread_id:
        params["message_thread_id"] = thread_id
    url = f"https://api.telegram.org/bot{token}/sendMessage?{urlencode(params)}"
    with urlopen(url, timeout=20) as res:  # nosec: token comes from local env, not logged
        payload = json.loads(res.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError("telegram sendMessage returned ok=false")


def check_watches(db_path: Path, *, live: bool = False, transport_log: Path | None = None, ensure: bool = True, upsert: tuple[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    now = utcnow()
    con = connect(db_path)
    if ensure:
        ensure_schema(con)
    if upsert:
        with con:
            upsert_watch(con, upsert[0], upsert[1], now)
    summary = {"ok": True, "db": str(db_path), "checked_at": now, "watches_checked": 0, "matches": 0, "alerts_sent": 0, "already_notified": 0, "errors": 0}
    with con:
        for watch in list_watches(con):
            summary["watches_checked"] += 1
            criteria = json.loads(watch["criteria_json"])
            rows = matching_listings(con, criteria)
            summary["matches"] += len(rows)
            sent_for_watch = 0
            max_new_alerts = alert_limit(criteria)
            for row in rows:
                inserted = con.execute(
                    """INSERT OR IGNORE INTO watch_notifications(watch_id, listing_id, notified_at, status, message_hash)
                    VALUES (?, ?, ?, 'pending', ?)""",
                    (watch["id"], row["id"], now, f"{watch['id']}:{row['id']}"),
                ).rowcount
                if inserted == 0:
                    summary["already_notified"] += 1
                    continue
                text = alert_text(watch, row)
                try:
                    send_transport(text, live=live, transport_log=transport_log)
                    con.execute("UPDATE watch_notifications SET status='sent', error=NULL WHERE watch_id=? AND listing_id=?", (watch["id"], row["id"]))
                    summary["alerts_sent"] += 1
                    sent_for_watch += 1
                    if sent_for_watch >= max_new_alerts:
                        break
                except Exception as exc:
                    con.execute("UPDATE watch_notifications SET status='failed', error=? WHERE watch_id=? AND listing_id=?", (str(exc)[:500], watch["id"], row["id"]))
                    summary["errors"] += 1
                    if live:
                        raise
            con.execute("UPDATE watches SET last_checked_at=? WHERE id=?", (now, watch["id"]))
    con.close()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase C P0 watch checker")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--ensure-schema", action="store_true", help="create watches tables if missing")
    ap.add_argument("--upsert-watch", help="create/update an active watch by name")
    ap.add_argument("--criteria-json", help="JSON criteria for --upsert-watch")
    ap.add_argument("--transport-log", help="append would-be Telegram sends to JSONL log instead of Telegram")
    ap.add_argument("--live", action="store_true", help="send real Telegram alerts")
    args = ap.parse_args()
    upsert = None
    if args.upsert_watch:
        if not args.criteria_json:
            raise SystemExit("--criteria-json is required with --upsert-watch")
        upsert = (args.upsert_watch, json.loads(args.criteria_json))
    report = check_watches(
        Path(args.db),
        live=args.live,
        transport_log=Path(args.transport_log) if args.transport_log else None,
        ensure=args.ensure_schema or bool(upsert),
        upsert=upsert,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
