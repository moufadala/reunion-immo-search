from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src import source_health


def _db(path):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE rental_listings (
        source_site TEXT, source_id TEXT, is_active INTEGER,
        seen_last_at TEXT, image_url TEXT
        )"""
    )
    return con


def test_recent_max_does_not_hide_low_active_listing_coverage(tmp_path):
    db = tmp_path / "watch.db"
    con = _db(db)
    rows = [
        ("leboncoin", str(i), 1, "2026-08-12T08:00:00+00:00" if i < 2 else "2026-08-01T08:00:00+00:00", "x.jpg")
        for i in range(10)
    ]
    con.executemany("INSERT INTO rental_listings VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()

    payload = source_health.build_payload(
        db,
        reference_time=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
    )
    item = next(x for x in payload["sources"] if x["source"] == "leboncoin")

    assert item["active_recent_rows"] == 2
    assert item["active_coverage_ratio"] == 0.2
    assert item["coverage_threshold"] == 0.9
    assert item["status"] == "coverage-low"
    assert item["severity"] == "high"
    assert payload["ok"] is False
    assert "leboncoin" in payload["summary"]["coverage_below_threshold"]


def test_full_recent_coverage_is_healthy(tmp_path):
    db = tmp_path / "watch.db"
    con = _db(db)
    con.executemany(
        "INSERT INTO rental_listings VALUES (?,?,?,?,?)",
        [("bienici", str(i), 1, "2026-08-12T08:00:00+00:00", "x.jpg") for i in range(10)],
    )
    con.commit()
    con.close()

    payload = source_health.build_payload(
        db,
        reference_time=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
    )
    item = next(x for x in payload["sources"] if x["source"] == "bienici")

    assert item["active_coverage_ratio"] == 1.0
    assert item["status"] == "fresh"
