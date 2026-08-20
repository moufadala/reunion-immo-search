from __future__ import annotations

import sqlite3

from scripts import realestate_watch


def test_observed_id_is_not_reinserted_as_missing_when_timestamp_is_old(tmp_path):
    db = tmp_path / "watch.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE rental_listings ("
            "source_site TEXT NOT NULL, source_id TEXT NOT NULL, "
            "seen_last_at TEXT, is_active INTEGER, rent_eur REAL, surface_m2 REAL, "
            "rooms REAL, bedrooms REAL, "
            "PRIMARY KEY(source_site, source_id))"
        )
        con.execute(
            "INSERT INTO rental_listings VALUES (?,?,?,1,NULL,NULL,NULL,NULL)",
            ("domimmo", "listing-1", "2026-08-01T00:00:00+00:00"),
        )

    result = realestate_watch.mark_stale_not_seen(
        db,
        "2026-08-18T10:00:00+00:00",
        ["domimmo"],
        run_id="complete-with-observation",
        observed_ids={"domimmo": {"listing-1"}},
    )

    with sqlite3.connect(db) as con:
        state = con.execute(
            "SELECT successful_missing_runs FROM source_absence_state "
            "WHERE source_site='domimmo' AND source_id='listing-1'"
        ).fetchone()
        active = con.execute(
            "SELECT is_active FROM rental_listings WHERE source_id='listing-1'"
        ).fetchone()[0]

    assert result["domimmo"] == 0
    assert state is None
    assert active == 1
