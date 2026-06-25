#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "saved_search_admin.py"

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    listings = tmp / "listings.json"
    listings.write_text(json.dumps({"meta":{},"listings":[
        {"id":"zimo:1","title":"T3 Moufia","region":"Nord","commune":"Saint-Denis","primary_zone":"Moufia","zones":["Moufia"],"property_type":"Appartement","furnished":"Non meublé","rent_eur":900,"surface_m2":70,"rooms":3,"bedrooms":2,"score":88,"db_is_canonical":True,"url":"https://example.test/1"},
        {"id":"zimo:2","title":"Studio sud","region":"Sud","commune":"Saint-Pierre","primary_zone":"Saint-Pierre","zones":["Saint-Pierre"],"property_type":"Appartement","furnished":"Meublé","rent_eur":500,"surface_m2":25,"rooms":1,"bedrooms":0,"score":20,"db_is_canonical":True,"url":"https://example.test/2"}
    ]}), encoding="utf-8")
    cfg = tmp / "saved.json"
    cfg.write_text(json.dumps({"version":1,"public_base_url":"https://immo.test/","max_items_per_search":6,"searches":[{"id":"nord","name":"Nord","enabled":True,"description":"test","filters":{"region":["Nord"],"zones":["Moufia"],"rentMax":1000,"surfaceMin":60,"tab":"all","sort":"score"}}]}), encoding="utf-8")
    out = tmp / "admin.json"; html = tmp / "saved.html"
    proc = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg), "--listings", str(listings), "--out", str(out), "--html-out", str(html)], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["searches"][0]["match_count"] == 1
    assert "Alertes & recherches" in html.read_text(encoding="utf-8")
print("SAVED_SEARCH_ADMIN_AUDIT PASS")
