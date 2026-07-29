#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_DB = Path(os.environ.get("IMMO_DB_PATH", "/opt/data/data/reunion_watch.db"))


def load_enrich_module():
    spec = importlib.util.spec_from_file_location("enrich_source_details_v3", ROOT / "scripts" / "enrich_source_details_v3.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_export(db: Path, out: Path) -> dict:
    env = {**os.environ, "IMMO_DB_PATH": str(db), "IMMO_FEED_OUT": str(out)}
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_feed.py")], cwd=ROOT, env=env, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return json.loads(out.read_text(encoding="utf-8"))


def main() -> int:
    if not REAL_DB.exists():
        print(f"AUDIT_EXPORT_FEED_LLM SKIP missing db {REAL_DB}")
        return 0
    with tempfile.TemporaryDirectory(prefix="immo-feed-llm-") as td:
        tmp = Path(td)
        db = tmp / "reunion_watch.db"
        out = tmp / "feed.json"
        shutil.copy2(REAL_DB, db)
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        enrich = load_enrich_module()
        rows = con.execute(
            """
            SELECT source_site, source_id, COALESCE(description,'') AS description
            FROM rental_listings
            WHERE COALESCE(is_active,1)=1
              AND source_site IN ('zimo','domimmo','locamoi','fnaim','bienici')
              AND length(COALESCE(description,'')) > 120
            ORDER BY seen_last_at DESC
            LIMIT 50
            """
        ).fetchall()
        if not rows:
            print("AUDIT_EXPORT_FEED_LLM SKIP no suitable listing")
            return 0
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS listing_llm_extraction (
                source_site TEXT NOT NULL,
                source_id TEXT NOT NULL,
                extracted_at TEXT NOT NULL,
                model TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                fields_json TEXT NOT NULL,
                grounded INTEGER NOT NULL DEFAULT 1,
                input_tokens INTEGER,
                output_tokens INTEGER,
                PRIMARY KEY (source_site, source_id, model)
            )
            """
        )
        fields = {
            "quartier_precis": "Quartier Test LLM",
            "proximites": ["École Test LLM"],
            "routes_axes": ["Route Test LLM"],
            "points_repere": ["Résidence Test LLM"],
        }
        for row in rows:
            con.execute(
                """
                INSERT OR REPLACE INTO listing_llm_extraction
                (source_site, source_id, extracted_at, model, input_hash, fields_json, grounded, input_tokens, output_tokens)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["source_site"], row["source_id"], "2026-07-29T00:00:00Z",
                    "anthropic/claude-haiku-4.5", enrich.llm_input_hash(row["description"]),
                    json.dumps(fields, ensure_ascii=False), 1, 10, 4,
                ),
            )
        con.commit(); con.close()
        payload = run_export(db, out)
        target_ids = {f"{row['source_site']}:{row['source_id']}" for row in rows}
        exported = [x for x in payload.get("listings", []) if x.get("id") in target_ids]
        if not exported:
            raise AssertionError(f"all {len(target_ids)} candidate rows excluded from public scope")
        stale_or_missing = [x.get("id") for x in exported if x.get("llm_extraction_status") != "fresh"]
        if stale_or_missing:
            raise AssertionError({"stale_or_missing": stale_or_missing[:10], "count": len(stale_or_missing)})
        wrong_fields = [x.get("id") for x in exported if (x.get("llm_extraction") or {}).get("routes_axes") != ["Route Test LLM"]]
        if wrong_fields:
            raise AssertionError({"wrong_fields": wrong_fields[:10], "count": len(wrong_fields)})
        if payload.get("meta", {}).get("avec_llm_extraction", 0) < 1:
            raise AssertionError(payload.get("meta"))

        probe_id = exported[0]["id"]
        probe_source, probe_sid = probe_id.split(":", 1)

        con = sqlite3.connect(db)
        con.execute(
            "UPDATE listing_llm_extraction SET input_hash=? WHERE source_site=? AND source_id=?",
            ("stale-input-hash", probe_source, probe_sid),
        )
        con.commit(); con.close()
        stale_payload = run_export(db, tmp / "feed_stale.json")
        stale_item = next(x for x in stale_payload.get("listings", []) if x.get("id") == probe_id)
        if stale_item.get("llm_extraction_status") != "stale" or stale_item.get("llm_extraction") is not None:
            raise AssertionError({"expected_stale_without_fields": stale_item.get("llm_extraction_status")})

        original = next(row for row in rows if f"{row['source_site']}:{row['source_id']}" == probe_id)
        con = sqlite3.connect(db)
        con.execute(
            "UPDATE listing_llm_extraction SET grounded=0, input_hash=? WHERE source_site=? AND source_id=?",
            (enrich.llm_input_hash(original["description"]), probe_source, probe_sid),
        )
        con.commit(); con.close()
        ungrounded_payload = run_export(db, tmp / "feed_ungrounded.json")
        ungrounded_item = next(x for x in ungrounded_payload.get("listings", []) if x.get("id") == probe_id)
        if ungrounded_item.get("llm_extraction_status") != "absent" or ungrounded_item.get("llm_extraction") is not None:
            raise AssertionError({"expected_ungrounded_absent": ungrounded_item.get("llm_extraction_status")})

        print(json.dumps({
            "status": "AUDIT_EXPORT_FEED_LLM PASS",
            "checked_exported_candidates": len(exported),
            "candidate_rows": len(rows),
            "avec_llm_extraction": payload.get("meta", {}).get("avec_llm_extraction"),
            "stale_guard_checked": probe_id,
            "ungrounded_guard_checked": probe_id,
            "out": str(out),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
