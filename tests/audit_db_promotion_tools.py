#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMOTE_DB = ROOT / "scripts" / "promote_db_candidate.py"
ROLLBACK_DB = ROOT / "scripts" / "rollback_db_candidate.py"


def make_db(path: Path, seloger: int = 5, other: int = 5) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE rental_listings (id TEXT PRIMARY KEY, source TEXT, is_active INTEGER)")
        for i in range(seloger):
            con.execute("INSERT INTO rental_listings VALUES (?, 'seloger', 1)", (f"s{i}",))
        for i in range(other):
            con.execute("INSERT INTO rental_listings VALUES (?, 'ofim', 1)", (f"o{i}",))
        con.commit()
    finally:
        con.close()


def count(path: Path) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM rental_listings WHERE is_active=1").fetchone()[0]
    finally:
        con.close()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="immo-db-promotion-test-") as td:
        tmp = Path(td)
        target = tmp / "prod.sqlite"
        candidate = tmp / "stage.sqlite"
        make_db(target, seloger=500, other=500)
        make_db(candidate, seloger=500, other=501)

        promote_json = tmp / "promote_db.json"
        p = subprocess.run([
            sys.executable, str(PROMOTE_DB),
            "--candidate", str(candidate),
            "--target", str(target),
            "--json-out", str(promote_json),
        ], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert p.returncode == 0, p.stdout + p.stderr
        payload = json.loads(promote_json.read_text(encoding="utf-8"))
        assert payload["ok"] and payload["promoted"] and payload["backup"], payload
        assert count(target) == 1001, count(target)

        rollback_json = tmp / "rollback_db.json"
        p = subprocess.run([
            sys.executable, str(ROLLBACK_DB),
            "--backup", payload["backup"],
            "--target", str(target),
            "--json-out", str(rollback_json),
        ], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert p.returncode == 0, p.stdout + p.stderr
        rollback = json.loads(rollback_json.read_text(encoding="utf-8"))
        assert rollback["ok"] and rollback["mode"] == "drill", rollback

        bad = tmp / "bad.sqlite"
        make_db(bad, seloger=1, other=1)
        bad_json = tmp / "bad_promote.json"
        p = subprocess.run([
            sys.executable, str(PROMOTE_DB),
            "--candidate", str(bad),
            "--target", str(target),
            "--json-out", str(bad_json),
        ], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert p.returncode != 0, p.stdout + p.stderr
        bad_payload = json.loads(bad_json.read_text(encoding="utf-8"))
        assert not bad_payload["ok"] and bad_payload["errors"], bad_payload

    print("IMMO_DB_PROMOTION_TOOLS_TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
