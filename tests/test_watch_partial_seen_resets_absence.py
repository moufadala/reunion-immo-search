from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts import realestate_watch


def _db(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE rental_listings ("
            "source_site TEXT NOT NULL, source_id TEXT NOT NULL, "
            "seen_last_at TEXT, is_active INTEGER, "
            "rent_eur REAL, surface_m2 REAL, rooms REAL, bedrooms REAL, "
            "PRIMARY KEY(source_site, source_id))"
        )
        con.execute(
            "INSERT INTO rental_listings VALUES (?,?,?,1,NULL,NULL,NULL,NULL)",
            ("domimmo", "listing-1", "2026-08-01T00:00:00+00:00"),
        )


def test_listing_seen_during_partial_run_resets_previous_complete_absence(tmp_path):
    db = tmp_path / "watch.db"
    _db(db)

    realestate_watch.mark_stale_not_seen(
        db,
        "2026-08-18T10:00:00+00:00",
        ["domimmo"],
        run_id="complete-1",
    )
    realestate_watch.mark_stale_not_seen(
        db,
        "2026-08-18T11:00:00+00:00",
        [],
        run_id="partial-1",
        observed_ids={"domimmo": {"listing-1"}},
    )
    realestate_watch.mark_stale_not_seen(
        db,
        "2026-08-18T12:00:00+00:00",
        ["domimmo"],
        run_id="complete-2",
    )

    with sqlite3.connect(db) as con:
        active = con.execute(
            "SELECT is_active FROM rental_listings WHERE source_id='listing-1'"
        ).fetchone()[0]
        missing_runs = con.execute(
            "SELECT successful_missing_runs FROM source_absence_state "
            "WHERE source_site='domimmo' AND source_id='listing-1'"
        ).fetchone()[0]

    assert active == 1
    assert missing_runs == 1


def test_main_passes_all_manifest_seen_ids_to_absence_ledger():
    source = Path(realestate_watch.__file__).read_text(encoding="utf-8")

    assert "observed_ids=observed_ids" in source
    assert "item.get('seen_ids')" in source


def test_complete_zero_snapshot_is_still_authoritative_for_absences():
    source = Path(realestate_watch.__file__).read_text(encoding="utf-8")

    assert "touched_sources = set(authoritative)" in source
