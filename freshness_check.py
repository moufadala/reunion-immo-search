#!/usr/bin/env python3
"""Phase D P0 observability: freshness dead-man switch + D2bis failed-send alert.

This script is designed to be run outside Hermes (system cron/container/host). It
can push a status to Uptime Kuma; Kuma then owns Telegram delivery.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "socle_p0.sqlite"
DEFAULT_STATE = Path("/opt/data/artifacts/socle-p0/phase-d/freshness_state.json")
DEFAULT_SOURCE = "immo_listings"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def ensure_runs_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            ran_at TEXT NOT NULL,
            statut TEXT NOT NULL,
            n_rows INTEGER NOT NULL DEFAULT 0,
            structure_hash TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_runs_source_ran_at ON runs(source, ran_at);
        CREATE INDEX IF NOT EXISTS idx_runs_statut ON runs(statut);
        """
    )


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def latest_run(con: sqlite3.Connection, source: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM runs WHERE source=? ORDER BY ran_at DESC, id DESC LIMIT 1", (source,)).fetchone()


def latest_ok_run(con: sqlite3.Connection, source: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM runs WHERE source=? AND statut='ok' AND n_rows>0 ORDER BY ran_at DESC, id DESC LIMIT 1", (source,)).fetchone()


def recent_ok_runs(con: sqlite3.Connection, source: str, limit: int = 14) -> list[sqlite3.Row]:
    return list(con.execute("SELECT * FROM runs WHERE source=? AND statut='ok' AND n_rows>0 ORDER BY ran_at DESC, id DESC LIMIT ?", (source, limit)))


def failed_notifications(con: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    if not table_exists(con, "watch_notifications"):
        return []
    return list(con.execute(
        """SELECT wn.id, wn.watch_id, wn.listing_id, wn.notified_at, wn.error,
                  COALESCE(w.name, 'watch ' || wn.watch_id) AS watch_name,
                  COALESCE(l.title, 'listing ' || wn.listing_id) AS listing_title,
                  l.url AS url
           FROM watch_notifications wn
           LEFT JOIN watches w ON w.id=wn.watch_id
           LEFT JOIN listings l ON l.id=wn.listing_id
           WHERE wn.status='failed'
           ORDER BY wn.notified_at DESC, wn.id DESC
           LIMIT ?""",
        (limit,),
    ))


def directory_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() or item.is_symlink():
                total += item.lstat().st_size
        except OSError:
            continue
    return total


def disk_alerts(*, artifacts_path: Path, artifact_size_threshold_gb: float, root_usage_threshold_pct: float) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    size_bytes = directory_size_bytes(artifacts_path)
    size_gb = size_bytes / (1024 ** 3)
    if size_gb > artifact_size_threshold_gb:
        alerts.append({
            "kind": "disk_immo_artifacts",
            "message": f"disque immo: artifacts/ {size_gb:.2f} Go > seuil {artifact_size_threshold_gb:.2f} Go",
            "size_gb": round(size_gb, 3),
            "threshold_gb": artifact_size_threshold_gb,
            "path": str(artifacts_path),
        })
    usage = shutil.disk_usage("/")
    pct = usage.used / usage.total * 100 if usage.total else 0.0
    if pct > root_usage_threshold_pct:
        alerts.append({
            "kind": "disk_immo_root",
            "message": f"disque immo: / {pct:.1f}% > seuil {root_usage_threshold_pct:.1f}%",
            "usage_pct": round(pct, 2),
            "threshold_pct": root_usage_threshold_pct,
        })
    return alerts


def build_report(db_path: Path, *, source: str, max_age_hours: float, collapse_ratio: float, reference_time: datetime | None = None, artifacts_path: Path | None = None, artifact_size_threshold_gb: float = 25.0, root_usage_threshold_pct: float = 85.0) -> dict[str, Any]:
    now = reference_time or utcnow()
    alerts: list[dict[str, Any]] = []
    con = connect(db_path)
    ensure_runs_schema(con)
    latest = latest_run(con, source)
    ok = latest_ok_run(con, source)
    recent = recent_ok_runs(con, source, 14)

    if latest is None:
        alerts.append({"kind": "no_runs", "message": f"source immo muette: aucun run enregistré pour {source}"})
    else:
        if latest["statut"] != "ok" or int(latest["n_rows"] or 0) <= 0:
            alerts.append({
                "kind": "latest_not_ok",
                "message": f"source immo muette: dernier run {source} statut={latest['statut']} n_rows={latest['n_rows']}",
                "run_id": latest["id"],
            })
    if ok is None:
        alerts.append({"kind": "no_ok_runs", "message": f"source immo muette: aucun run ok n_rows>0 pour {source}"})
    else:
        ran_at = parse_dt(ok["ran_at"])
        if not ran_at or now - ran_at > timedelta(hours=max_age_hours):
            alerts.append({
                "kind": "stale_ok_run",
                "message": f"source immo muette: aucun run ok récent pour {source} depuis {max_age_hours:g}h",
                "latest_ok_at": ok["ran_at"],
            })
    if len(recent) >= 2:
        newest = recent[0]
        previous = recent[1:]
        prev_hashes = {r["structure_hash"] for r in previous if r["structure_hash"]}
        if newest["structure_hash"] and prev_hashes and newest["structure_hash"] not in prev_hashes:
            alerts.append({
                "kind": "structure_hash_changed",
                "message": f"source immo muette: structure_hash changé pour {source} ({newest['structure_hash']})",
                "run_id": newest["id"],
            })
        prev_counts = [int(r["n_rows"] or 0) for r in previous if int(r["n_rows"] or 0) > 0]
        if prev_counts:
            med = statistics.median(prev_counts)
            if med and int(newest["n_rows"] or 0) < med * (1.0 - collapse_ratio):
                alerts.append({
                    "kind": "n_rows_collapse",
                    "message": f"source immo muette: n_rows effondré pour {source} ({newest['n_rows']} vs médiane {med:g})",
                    "run_id": newest["id"],
                })
    failed = failed_notifications(con)
    if failed:
        alerts.append({
            "kind": "watch_notification_failed",
            "message": f"D2bis: {len(failed)} ligne(s) watch_notifications.status='failed' — perte silencieuse Telegram possible",
            "failed": [dict(r) for r in failed],
        })
    artifacts_checked = artifacts_path or (ROOT / "artifacts")
    alerts.extend(disk_alerts(
        artifacts_path=artifacts_checked,
        artifact_size_threshold_gb=artifact_size_threshold_gb,
        root_usage_threshold_pct=root_usage_threshold_pct,
    ))
    con.close()
    status = "down" if alerts else "ok"
    msg = "OK immo freshness" if status == "ok" else "; ".join(a["message"] for a in alerts[:3])
    return {
        "ok": status == "ok",
        "status": status,
        "checked_at": iso(now),
        "db": str(db_path),
        "source": source,
        "latest_run": dict(latest) if latest else None,
        "latest_ok_run": dict(ok) if ok else None,
        "alerts": alerts,
        "message": msg,
    }


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def push_kuma(push_url: str, status: str, message: str, *, ping: float = 1.0) -> dict[str, Any]:
    sep = "&" if "?" in push_url else "?"
    url = f"{push_url}{sep}{urllib.parse.urlencode({'status': 'up' if status == 'ok' else 'down', 'msg': message[:450], 'ping': str(ping)})}"
    with urllib.request.urlopen(url, timeout=20) as res:  # nosec - local Kuma push URL from root-owned env
        body = res.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except Exception:
        payload = {"raw": body}
    return {"http_status": getattr(res, "status", None), "payload": payload}


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase D P0 freshness/dead-man checker")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--max-age-hours", type=float, default=26)
    ap.add_argument("--collapse-ratio", type=float, default=0.70)
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push-url")
    ap.add_argument("--push-env", default="/opt/data/services/uptime-kuma/phased_push.env")
    ap.add_argument("--artifacts-path", default=str(ROOT / "artifacts"))
    ap.add_argument("--artifact-size-threshold-gb", type=float, default=25.0)
    ap.add_argument("--root-usage-threshold-pct", type=float, default=85.0)
    args = ap.parse_args()

    report = build_report(
        Path(args.db),
        source=args.source,
        max_age_hours=args.max_age_hours,
        collapse_ratio=args.collapse_ratio,
        artifacts_path=Path(args.artifacts_path),
        artifact_size_threshold_gb=args.artifact_size_threshold_gb,
        root_usage_threshold_pct=args.root_usage_threshold_pct,
    )
    if not args.dry_run:
        state_path = Path(args.state)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        push_url = args.push_url
        if not push_url:
            env = load_env_file(Path(args.push_env))
            push_url = env.get("KUMA_PUSH_URL")
        if push_url:
            report["kuma_push"] = push_kuma(push_url, report["status"], report["message"])
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
