from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "listing_history.py"


def make_source(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE rental_listings (
        source_site TEXT, source_id TEXT, is_active INTEGER, title TEXT, url TEXT,
        seen_first_at TEXT, seen_last_at TEXT, rent_eur INTEGER, surface_m2 REAL,
        image_url TEXT, city TEXT, PRIMARY KEY(source_site, source_id))""")
    con.executemany("INSERT INTO rental_listings VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        ("portal", "visible", 1, "Visible", "https://x/1", "2026-08-01", "2026-08-10", 1200, 80, "p.jpg", "Saint-Denis"),
        ("portal", "filtered", 1, "Filtered", "https://x/2", "2026-08-01", "2026-08-10", 1300, 70, "p.jpg", "Saint-Denis"),
    ])
    con.commit()
    con.close()


def run(source: Path, history: Path, at: str) -> dict:
    result = subprocess.run([sys.executable, str(SCRIPT), "--source-db", str(source),
                             "--db", str(history), "--snapshot-at", at],
                            cwd=ROOT, check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_filters_and_photo_failures_cannot_create_disappearance(tmp_path: Path) -> None:
    source, history = tmp_path / "source.sqlite", tmp_path / "history.sqlite"
    make_source(source)
    assert run(source, history, "run-1")["counts"]["new"] == 2
    con = sqlite3.connect(source)
    con.execute("UPDATE rental_listings SET surface_m2=40,image_url=NULL WHERE source_site='portal' AND source_id='filtered'")
    con.commit()
    con.close()
    report = run(source, history, "run-2")
    assert report["counts"]["disappeared"] == 0
    con = sqlite3.connect(history)
    assert con.execute("SELECT active FROM listing_current WHERE id='portal:filtered'").fetchone()[0] == 1
    con.close()


def test_canonical_active_transitions_are_idempotent(tmp_path: Path) -> None:
    source, history = tmp_path / "source.sqlite", tmp_path / "history.sqlite"
    make_source(source)
    run(source, history, "run-1")
    con = sqlite3.connect(source)
    con.execute("UPDATE rental_listings SET is_active=0 WHERE source_site='portal' AND source_id='visible'")
    con.commit()
    con.close()
    assert run(source, history, "run-2")["counts"]["disappeared"] == 1
    assert run(source, history, "run-2")["counts"]["disappeared"] == 0
    con = sqlite3.connect(source)
    con.execute("UPDATE rental_listings SET is_active=1 WHERE source_site='portal' AND source_id='visible'")
    con.commit()
    con.close()
    assert run(source, history, "run-3")["counts"]["reappeared"] == 1
    con = sqlite3.connect(history)
    counts = dict(con.execute("SELECT event_type,COUNT(*) FROM listing_events WHERE listing_id='portal:visible' GROUP BY event_type"))
    con.close()
    assert counts == {"disappeared": 1, "new": 1, "reappeared": 1}


def test_same_source_id_on_two_portals_stays_distinct(tmp_path: Path) -> None:
    source, history = tmp_path / "source.sqlite", tmp_path / "history.sqlite"
    make_source(source)
    con = sqlite3.connect(source)
    con.execute(
        "INSERT INTO rental_listings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("other", "visible", 1, "Other", "https://y/1", "2026-08-01", "2026-08-10", 1250, 82, None, "Sainte-Marie"),
    )
    con.commit()
    con.close()

    assert run(source, history, "run-1")["counts"]["new"] == 3
    con = sqlite3.connect(history)
    ids = {row[0] for row in con.execute("SELECT id FROM listing_current")}
    con.close()
    assert {"portal:visible", "other:visible"} <= ids


def test_price_change_is_recorded_once(tmp_path: Path) -> None:
    source, history = tmp_path / "source.sqlite", tmp_path / "history.sqlite"
    make_source(source)
    run(source, history, "run-1")
    con = sqlite3.connect(source)
    con.execute(
        "UPDATE rental_listings SET rent_eur=1100 WHERE source_site='portal' AND source_id='visible'"
    )
    con.commit()
    con.close()

    report = run(source, history, "run-2")
    assert report["counts"]["price_changed"] == 1
    assert report["price_changes"][0]["delta"] == -100
    assert run(source, history, "run-2")["counts"]["price_changed"] == 0
