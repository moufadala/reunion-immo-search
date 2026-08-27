import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.immo_public_monitor import MIN_LISTINGS, feed_contract_errors


ROOT = Path(__file__).resolve().parents[1]


def listing(**overrides):
    row = {
        "id": "ok",
        "active": True,
        "surface": 70,
        "rent": 1500,
        "commune": "Saint-Denis",
        "quartier": "Centre",
    }
    row.update(overrides)
    return row


def test_monitor_volume_floor_matches_filtered_public_product():
    assert MIN_LISTINGS <= 194


def test_monitor_rejects_every_publication_policy_violation():
    data = {
        "listings": [
            listing(id="small", surface=64),
            listing(id="expensive", rent=1701),
            listing(id="wrong-city", commune="Sainte-Suzanne"),
            listing(id="providence", quartier="La Providence"),
        ]
    }
    errors = feed_contract_errors(data)
    assert len(errors) == 1
    assert "4 listing(s)" in errors[0]


def test_monitor_accepts_valid_scope_and_matching_movements():
    data = {
        "listings": [listing()],
        "meta": {"marche": {"retirees_7j": 2}},
        "movements": {"disparues_7j": 2},
    }
    assert feed_contract_errors(data) == []


def test_monitor_detects_movement_counter_drift():
    data = {
        "listings": [listing()],
        "meta": {"marche": {"retirees_7j": 1289}},
        "movements": {"disparues_7j": 21},
    }
    errors = feed_contract_errors(data)
    assert any("movement counters disagree" in error for error in errors)


def test_monitor_can_run_against_local_candidate_without_external_auth(tmp_path):
    app = tmp_path / "candidate"
    (app / "v2").mkdir(parents=True)
    (app / "index.html").write_text('<div id="root"></div><script src="/assets/app.js"></script>', encoding="utf-8")
    (app / "v2" / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    rows = [
        {
            "id": f"row-{idx}",
            "source": "seloger" if idx < 60 else "ofim",
            "active": True,
            "surface": 70,
            "rent": 1000,
            "commune": "Saint-Denis",
            "quartier": "Centre",
            "image": "/thumbs/a.jpg",
            "seen_last_at": datetime.now(timezone.utc).isoformat(),
        }
        for idx in range(MIN_LISTINGS)
    ]
    (app / "feed.json").write_text(
        json.dumps({"meta": {"genere_le": datetime.now(timezone.utc).isoformat()}, "listings": rows}),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "immo_public_monitor.py")],
        env={
            "IMMO_PUBLIC_MONITOR_APP_DIR": str(app),
            "IMMO_PUBLIC_MONITOR_SKIP_AUTH": "1",
            "PYTHONPATH": str(ROOT),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_monitor_rejects_feed_with_fresh_meta_but_stale_listing_observations(tmp_path):
    app = tmp_path / "candidate"
    (app / "v2").mkdir(parents=True)
    (app / "index.html").write_text('<div id="root"></div><script src="/assets/app.js"></script>', encoding="utf-8")
    (app / "v2" / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    stale_seen = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    rows = [
        {
            "id": f"row-{idx}",
            "source": "seloger" if idx < 60 else "ofim",
            "active": True,
            "surface": 70,
            "rent": 1000,
            "commune": "Saint-Denis",
            "quartier": "Centre",
            "image": "/thumbs/a.jpg",
            "seen_last_at": stale_seen,
        }
        for idx in range(MIN_LISTINGS)
    ]
    (app / "feed.json").write_text(
        json.dumps({"meta": {"genere_le": datetime.now(timezone.utc).isoformat()}, "listings": rows}),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "immo_public_monitor.py")],
        env={
            "IMMO_PUBLIC_MONITOR_APP_DIR": str(app),
            "IMMO_PUBLIC_MONITOR_SKIP_AUTH": "1",
            "PYTHONPATH": str(ROOT),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert proc.returncode == 2
    assert "listing observations stale" in proc.stdout
