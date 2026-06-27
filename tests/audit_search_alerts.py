#!/usr/bin/env python3
"""Regression audit for src/search_alerts.py.

Uses synthetic listings + temporary state/history DB so the test is stable and
never depends on today's production search matches.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "search_alerts.py"
BASE_CONFIG = ROOT / "config" / "saved_searches.json"

errors: list[str] = []
if not SCRIPT.exists():
    errors.append("missing src/search_alerts.py")
if not BASE_CONFIG.exists():
    errors.append("missing config/saved_searches.json")
if errors:
    print("SEARCH_ALERTS_AUDIT FAIL", errors)
    sys.exit(1)

LISTINGS = {
    "meta": {"generated_at": "2026-06-24T00:00:00+00:00"},
    "count": 6,
    "listings": [
        {
            "id": "synthetic:nord-1",
            "title": "Appartement T3 Sainte-Marie La Bretagne",
            "city": "Sainte-Marie",
            "commune": "Sainte-Marie",
            "region": "Nord",
            "primary_zone": "La Bretagne",
            "zones": ["Sainte-Marie", "La Bretagne"],
            "property_type": "Appartement",
            "furnished": "Non meublé",
            "rent_eur": 900,
            "surface_m2": 68,
            "rooms": 3,
            "bedrooms": 2,
            "location_label": "Sainte-Marie · La Bretagne",
            "url": "https://example.test/nord-1",
            "score": 87,
            "decision_summary": "fixture matching nord familial",
        },
        {
            "id": "synthetic:sud-1",
            "title": "Maison Saint-Pierre 65m2",
            "city": "Saint-Pierre",
            "commune": "Saint-Pierre",
            "region": "Sud",
            "property_type": "Maison",
            "furnished": "Non précisé",
            "rent_eur": 850,
            "surface_m2": 65,
            "rooms": 3,
            "bedrooms": 2,
            "location_label": "Saint-Pierre",
            "url": "https://example.test/sud-1",
            "score": 72,
            "decision_summary": "fixture matching sud budget",
        },
        {
            "id": "synthetic:nonmatch",
            "title": "Studio trop petit",
            "city": "Saint-Denis",
            "commune": "Saint-Denis",
            "region": "Nord",
            "property_type": "Appartement",
            "furnished": "Meublé",
            "rent_eur": 1200,
            "surface_m2": 25,
            "rooms": 1,
            "bedrooms": 0,
            "url": "https://example.test/nonmatch",
            "score": 40,
        },
        {
            "id": "synthetic:false-beausejour-living-room",
            "title": "location appartement 50m saint gilles les bains 97434",
            "city": "Sainte-Marie",
            "region": "Nord",
            "district": "Beauséjour",
            "location": "Beauséjour · Sainte-Marie",
            "property_type": "Appartement",
            "furnished": "Non précisé",
            "rent_eur": 950,
            "surface_m2": 50,
            "rooms": 2,
            "bedrooms": 1,
            "description": "Appartement Charmant 2 pièces 50m² Saint-Paul Saint-Gilles-les-Bains avec un beau séjour donnant sur varangue.",
            "url": "https://www.superimmo.com/annonces/location-appartement-50m-saint-gilles-les-bains-97434-x11hp27",
            "location_intelligence": {"commune_inferred": "Sainte-Marie", "district_best": "Beauséjour"},
            "score": 74,
        },
        {
            "id": "synthetic:grande-montee-contract-alias",
            "title": "T4 non meublé La Grande Montée Sainte-Marie",
            "city": "Sainte-Marie",
            "commune": "Sainte-Marie",
            "region": "Nord",
            "property_type": "Appartement",
            "furnished": "Non meublé",
            "rent_eur": 980,
            "surface_m2": 82,
            "rooms": 4,
            "bedrooms": 3,
            "description": "Appartement familial secteur grande montée, proche écoles.",
            "url": "https://example.test/grande-montee",
            "score": 83,
        },
        {
            "id": "synthetic:real-beausejour",
            "title": "T2 Beauséjour Sainte-Marie",
            "city": "Sainte-Marie",
            "commune": "Sainte-Marie",
            "region": "Nord",
            "district": "Beauséjour",
            "property_type": "Appartement",
            "furnished": "Non précisé",
            "rent_eur": 900,
            "surface_m2": 48,
            "rooms": 2,
            "bedrooms": 1,
            "description": "Appartement à Beauséjour Sainte-Marie, proche commodités.",
            "url": "https://example.test/beausejour-sainte-marie",
            "score": 82,
        },
    ],
}

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    cfg = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    cfg["source_json"] = str(tmp / "listings.json")
    cfg["state_path"] = str(tmp / "seen.json")
    cfg["history_db"] = str(tmp / "history.sqlite")
    cfg["min_score"] = None
    cfg["searches"] = [
        {
            "id": "nord_fixture",
            "name": "Nord fixture",
            "enabled": True,
            "filters": {
                "region": ["Nord"],
                "zones": ["Sainte-Marie", "La Bretagne", "Saint-Denis"],
                "property_type": ["Appartement", "Maison", "Appartement / maison"],
                "furnished": ["Non meublé", "Non précisé"],
                "rentMin": 600,
                "rentMax": 1000,
                "surfaceMin": 60,
                "roomsMin": 2,
                "bedroomsMin": 1,
                "sort": "score",
            },
        },
        {
            "id": "sud_fixture",
            "name": "Sud fixture",
            "enabled": True,
            "filters": {"region": ["Sud"], "rentMax": 900, "surfaceMin": 50, "sort": "priceAsc"},
        },
        {
            "id": "contract_alias_fixture",
            "name": "Contract alias fixture",
            "enabled": True,
            "filters": {"region": ["Nord"], "zones": ["Grande Montee"], "rentMax": 1100, "sort": "score"},
        },
        {
            "id": "commune_alias_conflict_fixture",
            "name": "Commune alias conflict fixture",
            "enabled": True,
            "filters": {"region": ["Nord"], "zones": ["Sainte Marie"], "rentMax": 1000, "sort": "score"},
        },
        {
            "id": "beausejour_direct_conflict_fixture",
            "name": "Beausejour direct conflict fixture",
            "enabled": True,
            "filters": {"region": ["Nord"], "zones": ["Beausejour"], "rentMax": 1000, "sort": "score"},
        },
    ]
    Path(cfg["source_json"]).write_text(json.dumps(LISTINGS, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg_path = tmp / "saved_searches.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    dry = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path), "--dry-run"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if dry.returncode != 0:
        errors.append(f"dry-run failed rc={dry.returncode} stderr={dry.stderr[:300]}")
        data = {"searches": []}
    else:
        data = json.loads(dry.stdout)
        if not data.get("ok") or not data.get("dry_run"):
            errors.append("dry-run JSON missing ok/dry_run")
        if "state_path" not in data or "would_emit" not in data:
            errors.append("dry-run JSON missing operator summary fields")
        for s in data.get("searches") or []:
            if "immo=1" not in s.get("search_url", ""):
                errors.append(f"search URL is not restorable for {s.get('id')}")
            if s.get("matches", 0) <= 0:
                errors.append(f"synthetic search has no matches: {s.get('id')}")
            if s.get("id") == "nord_fixture" and "synthetic:false-beausejour-living-room" in (s.get("top_ids") or []):
                errors.append("Saint-Gilles listing with stale Beauséjour intelligence must not match Sainte-Marie saved search")
            if s.get("id") == "contract_alias_fixture" and "synthetic:grande-montee-contract-alias" not in (s.get("top_ids") or []):
                errors.append("Grande Montee saved-search zone should match La Grande Montée through shared geo/search contract aliases")
            if s.get("id") in {"commune_alias_conflict_fixture", "beausejour_direct_conflict_fixture"}:
                top_ids = s.get("top_ids") or []
                if "synthetic:false-beausejour-living-room" in top_ids:
                    errors.append(f"{s.get('id')} must veto Saint-Gilles text even when zone uses a contract alias")
                if s.get("id") == "beausejour_direct_conflict_fixture" and "synthetic:real-beausejour" not in top_ids:
                    errors.append("direct Beausejour zone should still match a real Beauséjour Sainte-Marie listing")

    preview = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path), "--preview"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if preview.returncode != 0:
        errors.append(f"preview failed rc={preview.returncode} stderr={preview.stderr[:300]}")
    if "Alertes sauvegardées — preview" not in preview.stdout or "Émission potentielle" not in preview.stdout:
        errors.append("human preview missing readable operator labels")
    if Path(cfg["state_path"]).exists():
        errors.append("preview must not create state")

    first = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path)], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if first.returncode != 0:
        errors.append(f"first run failed rc={first.returncode} stderr={first.stderr[:300]}")
    if first.stdout.strip():
        errors.append("first non-dry run should bootstrap silently, not spam")
    if not Path(cfg["state_path"]).exists():
        errors.append("state file not created on first run")

    status = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path), "--status"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if status.returncode != 0:
        errors.append(f"status failed rc={status.returncode} stderr={status.stderr[:300]}")
    if "Alertes sauvegardées — état" not in status.stdout or "Recherches actives/configurées" not in status.stdout:
        errors.append("status output missing readable state labels")

    boot = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path), "--bootstrap-silent", "--format", "json"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if boot.returncode != 0:
        errors.append(f"explicit bootstrap-silent failed rc={boot.returncode} stderr={boot.stderr[:300]}")
    else:
        boot_data = json.loads(boot.stdout)
        if not boot_data.get("bootstrap_silent") or not boot_data.get("backup_path"):
            errors.append("bootstrap-silent should report explicit mode and backup_path when state exists")
        if "🏠" in boot.stdout or "nouvelle(s)" in boot.stdout:
            errors.append("bootstrap-silent must not emit alert digest")

    second = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path), "--verbose"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if second.returncode != 0:
        errors.append(f"second run failed rc={second.returncode} stderr={second.stderr[:300]}")
    try:
        second_data = json.loads(second.stdout)
        if second_data.get("new") != 0:
            errors.append("second run should have zero new listings after bootstrap")
    except Exception as exc:
        errors.append(f"second verbose output not JSON: {exc}")

    # Controlled live mode must refuse noisy output before writing state, then
    # allow a capped one-shot digest when explicitly under the cap. No Telegram
    # transport is invoked here; live mode only prints the digest to stdout.
    listings_with_new = json.loads(Path(cfg["source_json"]).read_text(encoding="utf-8"))
    new_item = dict(LISTINGS["listings"][0])
    new_item["id"] = "synthetic:nord-2"
    new_item["title"] = "Appartement T3 Sainte-Marie nouveau live"
    new_item["url"] = "https://example.test/nord-2"
    listings_with_new["listings"].append(new_item)
    Path(cfg["source_json"]).write_text(json.dumps(listings_with_new, ensure_ascii=False, indent=2), encoding="utf-8")
    state_before_cap = Path(cfg["state_path"]).read_text(encoding="utf-8")
    capped = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path), "--live", "--max-emit-total", "0"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if capped.returncode != 2 or "live_emit_cap_exceeded" not in capped.stderr:
        errors.append(f"live cap should refuse noisy output rc={capped.returncode} stderr={capped.stderr[:300]}")
    if Path(cfg["state_path"]).read_text(encoding="utf-8") != state_before_cap:
        errors.append("live cap refusal must not write state")
    live_ok = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path), "--live", "--max-emit-total", "3"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if live_ok.returncode != 0:
        errors.append(f"controlled live failed rc={live_ok.returncode} stderr={live_ok.stderr[:300]}")
    if "nouvelle(s)" not in live_ok.stdout or ("nouveau live" not in live_ok.stdout and "https://example.test/nord-2" not in live_ok.stdout):
        errors.append(f"controlled live should emit the one new listing digest, got: {live_ok.stdout[:300]}")

    # Price/status events from the history DB should be sent only when newer
    # than last_checked_at and matching that search.
    try:
        event_listing_id = "synthetic:nord-1"
        event_item = next(x for x in LISTINGS["listings"] if x["id"] == event_listing_id)
        state_path = Path(cfg["state_path"])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["searches"]["nord_fixture"]["last_checked_at"] = "2026-01-01T00:00:00+00:00"
        state["searches"]["sud_fixture"]["last_checked_at"] = "2026-01-01T00:00:00+00:00"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        con = sqlite3.connect(cfg["history_db"])
        with con:
            con.executescript(
                """
                CREATE TABLE listing_current (id TEXT PRIMARY KEY, raw_json TEXT NOT NULL);
                CREATE TABLE listing_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    details_json TEXT
                );
                """
            )
            con.execute("INSERT INTO listing_current(id, raw_json) VALUES (?,?)", (event_listing_id, json.dumps(event_item, ensure_ascii=False, sort_keys=True)))
            con.execute(
                "INSERT INTO listing_events(listing_id,event_type,event_at,old_value,new_value,details_json) VALUES (?,?,?,?,?,?)",
                (event_listing_id, "price_changed", "2026-02-01T00:00:00+00:00", "1000", "900", json.dumps({"title": event_item.get("title"), "url": event_item.get("url")}, ensure_ascii=False)),
            )
        con.close()
        event_run = subprocess.run([sys.executable, str(SCRIPT), "--config", str(cfg_path)], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if event_run.returncode != 0:
            errors.append(f"event run failed rc={event_run.returncode} stderr={event_run.stderr[:300]}")
        if "📉 Baisse" not in event_run.stdout or "1000€ → 900€" not in event_run.stdout:
            errors.append(f"expected price-drop digest, got: {event_run.stdout[:500]}")
    except Exception as exc:
        errors.append(f"history event setup/check failed: {exc}")

print("SEARCH_ALERTS_AUDIT", "PASS" if not errors else "FAIL")
if not errors:
    print(json.dumps({"dry_run_searches": data.get("searches", [])}, ensure_ascii=False, indent=2))
else:
    for e in errors:
        print(" -", e)
    sys.exit(1)
