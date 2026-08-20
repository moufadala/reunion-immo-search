from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src import source_health


def test_leboncoin_low_listing_coverage_blocks_publication(tmp_path):
    db = tmp_path / "watch.db"
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE rental_listings (
        source_site TEXT, source_id TEXT, is_active INTEGER,
        seen_last_at TEXT, image_url TEXT
        )"""
    )
    con.executemany(
        "INSERT INTO rental_listings VALUES (?,?,?,?,?)",
        [
            ("leboncoin", str(i), 1,
             "2026-08-12T08:00:00+00:00" if i < 2 else "2026-08-01T08:00:00+00:00",
             "x.jpg")
            for i in range(20)
        ],
    )
    con.commit()
    con.close()

    payload = source_health.build_payload(
        db, reference_time=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    )

    assert payload["ok"] is False
    assert "leboncoin" in payload["summary"]["coverage_below_threshold"]
