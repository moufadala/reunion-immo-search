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


def run(source: Path, history: Path, at: str, *, outcome: str = "complete") -> dict:
    con = sqlite3.connect(source)
    observed: dict[str, list[str]] = {}
    for source_site, source_id, active in con.execute(
        "SELECT source_site,source_id,is_active FROM rental_listings"
    ):
        observed.setdefault(source_site, [])
        if active:
            observed[source_site].append(source_id)
    con.close()
    manifest = source.parent / f"manifest-{at}-{outcome}.json"
    manifest.write_text(json.dumps({
        "run_id": at,
        "sources": {
            name: {"outcome": outcome, "seen_ids": source_ids}
            for name, source_ids in observed.items()
        },
    }), encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT), "--source-db", str(source),
                             "--db", str(history), "--snapshot-at", at,
                             "--source-run-manifest", str(manifest)],
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
    first_absence = run(source, history, "run-2")
    assert first_absence["counts"]["disappeared"] == 1
    assert first_absence["counts"]["missing_pending"] == 0
    assert run(source, history, "run-2")["counts"]["disappeared"] == 0
    assert run(source, history, "run-3")["counts"]["disappeared"] == 0
    con = sqlite3.connect(source)
    con.execute("UPDATE rental_listings SET is_active=1 WHERE source_site='portal' AND source_id='visible'")
    con.commit()
    con.close()
    assert run(source, history, "run-4")["counts"]["reappeared"] == 1
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


def test_partial_and_failed_runs_cannot_advance_missing_streak(tmp_path: Path) -> None:
    source, history = tmp_path / "source.sqlite", tmp_path / "history.sqlite"
    make_source(source)
    run(source, history, "run-1")
    con = sqlite3.connect(source)
    con.execute("UPDATE rental_listings SET is_active=0 WHERE source_site='portal' AND source_id='visible'")
    con.commit()
    con.close()

    assert run(source, history, "run-2", outcome="partial")["counts"]["disappeared"] == 0
    assert run(source, history, "run-3", outcome="failed")["counts"]["disappeared"] == 0
    first_complete = run(source, history, "run-4")

    con = sqlite3.connect(history)
    active, streak, state = con.execute(
        "SELECT active,successful_missing_runs,lifecycle_state "
        "FROM listing_current WHERE id='portal:visible'"
    ).fetchone()
    con.close()
    assert first_complete["counts"]["missing_pending"] == 0
    assert first_complete["counts"]["disappeared"] == 1
    assert (active, streak, state) == (0, 1, "withdrawn")


def test_manifest_without_explicit_complete_is_non_authoritative(tmp_path: Path) -> None:
    source, history = tmp_path / "source.sqlite", tmp_path / "history.sqlite"
    make_source(source)
    run(source, history, "run-1")
    con = sqlite3.connect(source)
    con.execute("UPDATE rental_listings SET is_active=0 WHERE source_site='portal' AND source_id='visible'")
    con.commit()
    con.close()

    # Execution success is deliberately insufficient: it does not prove completeness.
    manifest = tmp_path / "legacy-manifest.json"
    manifest.write_text(json.dumps({
        "run_id": "run-2",
        "source_status": {"portal": {"ok": True, "count": 1}},
    }), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-db",
            str(source),
            "--db",
            str(history),
            "--snapshot-at",
            "run-2",
            "--source-run-manifest",
            str(manifest),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert json.loads(result.stdout)["counts"]["disappeared"] == 0


def test_partial_run_can_reappear_seen_listing_and_replay_is_stable(tmp_path: Path) -> None:
    source, history = tmp_path / "source.sqlite", tmp_path / "history.sqlite"
    make_source(source)
    run(source, history, "run-1")
    con = sqlite3.connect(source)
    con.execute("UPDATE rental_listings SET is_active=0 WHERE source_id='visible'")
    con.commit()
    con.close()
    run(source, history, "run-2")
    run(source, history, "run-3")

    con = sqlite3.connect(source)
    con.execute("UPDATE rental_listings SET is_active=1 WHERE source_id='visible'")
    con.commit()
    con.close()
    appeared = run(source, history, "run-4", outcome="partial")
    replay = run(source, history, "run-4", outcome="partial")

    assert appeared["counts"]["reappeared"] == 1
    assert replay["counts"]["reappeared"] == 0
    con = sqlite3.connect(history)
    state = con.execute(
        "SELECT lifecycle_state FROM listing_current WHERE id='portal:visible'"
    ).fetchone()[0]
    events = con.execute(
        "SELECT COUNT(*) FROM listing_events "
        "WHERE listing_id='portal:visible' AND event_type='reappeared'"
    ).fetchone()[0]
    con.close()
    assert state == "reappeared"
    assert events == 1


def test_reconciliation_sources_list_shape_is_consumed(tmp_path: Path) -> None:
    source, history = tmp_path / "source.sqlite", tmp_path / "history.sqlite"
    make_source(source)
    manifest = tmp_path / "reconciliation.json"
    manifest.write_text(
        json.dumps({
            "run_id": "run-1",
            "sources": [
                {"run_id": "run-1", "source": "portal", "status": "complete"}
            ],
        }),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-db",
            str(source),
            "--db",
            str(history),
            "--snapshot-at",
            "ignored-in-favour-of-manifest-run-id",
            "--source-run-manifest",
            str(manifest),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    report = json.loads(result.stdout)

    assert report["source_run_id"] == "run-1"
    assert report["authoritative_sources"] == ["portal"]
    assert report["counts"]["new"] == 2


def test_same_run_can_upgrade_partial_to_complete_once(tmp_path: Path) -> None:
    source, history = tmp_path / "source.sqlite", tmp_path / "history.sqlite"
    make_source(source)
    run(source, history, "run-1")
    con = sqlite3.connect(source)
    con.execute(
        "UPDATE rental_listings SET is_active=0 "
        "WHERE source_site='portal' AND source_id='visible'"
    )
    con.commit()
    con.close()

    partial = run(source, history, "run-2", outcome="partial")
    completed = run(source, history, "run-2", outcome="complete")
    replay = run(source, history, "run-2", outcome="complete")

    assert partial["counts"]["missing_pending"] == 0
    assert completed["counts"]["missing_pending"] == 0
    assert completed["counts"]["disappeared"] == 1
    assert replay["counts"]["missing_pending"] == 0
    con = sqlite3.connect(history)
    state = con.execute(
        "SELECT lifecycle_state,successful_missing_runs FROM listing_current "
        "WHERE id='portal:visible'"
    ).fetchone()
    ledger = con.execute(
        "SELECT outcome FROM source_run_ledger WHERE source_site='portal' AND run_id='run-2'"
    ).fetchone()[0]
    con.close()
    assert state == ("withdrawn", 1)
    assert ledger == "complete"
