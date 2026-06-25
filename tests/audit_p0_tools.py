#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEDUP = ROOT / "scripts" / "audit_dedup_public.py"
SUMMARY = ROOT / "scripts" / "generate_daily_summary.py"
ROLLBACK = ROOT / "scripts" / "rollback_public_app.py"
PROMOTE = ROOT / "scripts" / "promote_app_candidate.py"


def write_app(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text("<html>Recherche immo RUN</html>", encoding="utf-8")
    rows = [
        {"source": "seloger", "title": "T2 Saint-Denis", "city": "Saint-Denis", "price": 800, "surface_m2": 45, "rooms": 2, "url": "https://a/1", "description": "desc", "local_image_url": "thumbs/a.jpg"},
        {"source": "ofim", "title": "T2 Saint-Denis", "city": "Saint-Denis", "price": 805, "surface_m2": 46, "rooms": 2, "url": "https://b/1", "description": "desc", "local_image_url": "thumbs/b.jpg"},
        {"source": "keldom", "title": "T3 Saint-Paul", "city": "Saint-Paul", "price": 950, "surface_m2": 70, "rooms": 3, "url": "https://c/1", "description": "desc", "local_image_url": "thumbs/c.jpg"},
    ]
    (path / "listings.json").write_text(json.dumps({"listings": rows}), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="immo-p0-tools-test-") as td:
        tmp = Path(td)
        app = tmp / "app"
        write_app(app)
        dedup_json = tmp / "dedup.json"
        dedup_md = tmp / "dedup.md"
        p = subprocess.run([sys.executable, str(DEDUP), "--listings", str(app / "listings.json"), "--json-out", str(dedup_json), "--md-out", str(dedup_md)], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert p.returncode == 0, p.stdout + p.stderr
        assert dedup_json.exists() and dedup_md.exists()
        data = json.loads(dedup_json.read_text(encoding="utf-8"))
        assert data["duplicate_groups"] >= 1, data

        sdir = tmp / "summary"
        p = subprocess.run([sys.executable, str(SUMMARY), "--app", str(app), "--out-dir", str(sdir)], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert p.returncode == 0, p.stdout + p.stderr
        assert (sdir / "summary.json").exists() and (sdir / "summary.md").exists()

        drill = tmp / "rollback.json"
        p = subprocess.run([sys.executable, str(ROLLBACK), "--backup", str(app), "--target", str(tmp / "target"), "--json-out", str(drill)], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert p.returncode == 0, p.stdout + p.stderr
        payload = json.loads(drill.read_text(encoding="utf-8"))
        assert payload["mode"] == "drill" and payload["ok"], payload

        promote_json = tmp / "promote.json"
        p = subprocess.run([sys.executable, str(PROMOTE), "--candidate", str(app), "--target", str(app), "--dry-run", "--json-out", str(promote_json)], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert p.returncode == 0, p.stdout + p.stderr
        payload = json.loads(promote_json.read_text(encoding="utf-8"))
        assert payload["dry_run"] and payload["ok"], payload
    print("IMMO_P0_TOOLS_TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
