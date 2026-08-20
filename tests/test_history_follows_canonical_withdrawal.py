from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.realestate_watch import mark_stale_not_seen
from src.listing_history import canonical_snapshot


def _source_db(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE rental_listings ("
            "source_site TEXT, source_id TEXT, is_active INTEGER, title TEXT, "
            "url TEXT, seen_first_at TEXT, seen_last_at TEXT, rent_eur INTEGER, "
            "surface_m2 REAL, rooms REAL, bedrooms REAL, image_url TEXT, city TEXT, "
            "PRIMARY KEY(source_site, source_id))"
        )
        con.execute(
            "INSERT INTO rental_listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "portal",
                "listing-1",
                1,
                "Annonce",
                "https://example.test/1",
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
                1200,
                80,
                3,
                2,
                "photo.jpg",
                "Saint-Denis",
            ),
        )


def _manifest(path: Path, run_id: str, *, seen: bool) -> Path:
    target = path / f"manifest-{run_id}.json"
    target.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "sources": [
                    {
                        "source": "portal",
                        "status": "complete",
                        "run_id": run_id,
                        "seen_ids": ["listing-1"] if seen else [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return target


def test_history_emits_when_two_run_canonical_ledger_actually_withdraws(tmp_path):
    source = tmp_path / "source.sqlite"
    history = tmp_path / "history.sqlite"
    _source_db(source)

    initial = canonical_snapshot(
        source, history, "run-0", _manifest(tmp_path, "run-0", seen=True)
    )
    assert initial["counts"]["new"] == 1

    first = mark_stale_not_seen(
        source,
        "2026-08-18T10:00:00+00:00",
        ["portal"],
        run_id="run-1",
    )
    after_first = canonical_snapshot(
        source, history, "run-1", _manifest(tmp_path, "run-1", seen=False)
    )
    assert first == {"portal": 0}
    assert after_first["counts"]["disappeared"] == 0

    second = mark_stale_not_seen(
        source,
        "2026-08-18T11:00:00+00:00",
        ["portal"],
        run_id="run-2",
    )
    after_second = canonical_snapshot(
        source, history, "run-2", _manifest(tmp_path, "run-2", seen=False)
    )

    assert second == {"portal": 1}
    assert after_second["counts"]["disappeared"] == 1
    with sqlite3.connect(history) as con:
        assert con.execute(
            "SELECT active FROM listing_current WHERE id='portal:listing-1'"
        ).fetchone()[0] == 0
