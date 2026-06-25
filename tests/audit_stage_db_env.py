#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROD_DB = Path(os.environ.get("IMMO_PROD_DB_PATH", "/opt/data/data/reunion_watch.db"))


def count_rows(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM rental_listings").fetchone()[0]
    finally:
        con.close()


def integrity(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()


def main() -> int:
    if not PROD_DB.exists():
        raise SystemExit(f"prod db missing: {PROD_DB}")
    prod_before = count_rows(PROD_DB)
    with tempfile.TemporaryDirectory(prefix="immo-stage-db-audit-") as td:
        tmp = Path(td)
        stage_db = tmp / "stage.sqlite"
        app = tmp / "app"
        shutil.copy2(PROD_DB, stage_db)
        env = os.environ.copy()
        env["IMMO_DB_PATH"] = str(stage_db)
        env["IMMO_APP_PATH"] = str(app)
        env["PYTHONPYCACHEPREFIX"] = env.get("PYTHONPYCACHEPREFIX", "/tmp/pycache-hermes")
        subprocess.run([sys.executable, "src/build_app.py"], cwd=ROOT, env=env, check=True)
        data = json.loads((app / "listings.json").read_text(encoding="utf-8"))
        listings = data.get("listings") or []
        prod_after = count_rows(PROD_DB)
        stage_after = count_rows(stage_db)
        assert prod_before == prod_after, {"prod_before": prod_before, "prod_after": prod_after}
        assert stage_after == prod_before, {"stage_after": stage_after, "prod_before": prod_before}
        assert integrity(stage_db) == "ok"
        assert len(listings) >= 400, len(listings)
        assert (app / "source_health.json").exists()
        print(json.dumps({
            "ok": True,
            "prod_db": str(PROD_DB),
            "stage_db": str(stage_db),
            "prod_rows": prod_after,
            "stage_rows": stage_after,
            "listings": len(listings),
            "app": str(app),
        }, ensure_ascii=False))
    print("STAGE_DB_ENV_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
