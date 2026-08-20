from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

from scripts import realestate_watch


def _watch_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE rental_listings (
        source_site TEXT, source_id TEXT, is_active INTEGER,
        seen_last_at TEXT, rent_eur INTEGER, surface_m2 REAL,
        rooms INTEGER, bedrooms INTEGER
        )"""
    )
    con.executemany(
        "INSERT INTO rental_listings VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                "domimmo",
                str(i),
                1,
                "2026-08-11T10:00:00+00:00" if i >= 4 else "2026-08-12T10:01:00+00:00",
                900,
                50.0,
                2,
                1,
            )
            for i in range(43)
        ],
    )
    con.commit()
    con.close()


def test_complete_scope_deactivates_only_after_two_distinct_complete_runs(tmp_path):
    db = tmp_path / "watch.db"
    _watch_db(db)

    first = realestate_watch.mark_stale_not_seen(
        db,
        "2026-08-12T10:00:00+00:00",
        ["domimmo"],
        {"domimmo": 4},
        run_id="run-1",
    )
    replay = realestate_watch.mark_stale_not_seen(
        db, "2026-08-12T10:00:00+00:00", ["domimmo"], {"domimmo": 4}, run_id="run-1"
    )
    partial = realestate_watch.mark_stale_not_seen(
        db, "2026-08-12T10:00:00+00:00", [], {"domimmo": 0}, run_id="run-partial"
    )
    second = realestate_watch.mark_stale_not_seen(
        db, "2026-08-12T10:00:00+00:00", ["domimmo"], {"domimmo": 4}, run_id="run-2"
    )

    con = sqlite3.connect(db)
    active = con.execute(
        "SELECT COUNT(*) FROM rental_listings WHERE source_site='domimmo' AND is_active=1"
    ).fetchone()[0]
    con.close()
    assert first == {"domimmo": 0}
    assert replay == {"domimmo": 0}
    assert partial == {}
    assert second == {"domimmo": 39}
    assert active == 4


def test_seen_listing_resets_its_missing_counter(tmp_path):
    db = tmp_path / "watch.db"
    _watch_db(db)
    realestate_watch.mark_stale_not_seen(
        db, "2026-08-12T10:00:00+00:00", ["domimmo"], run_id="run-1"
    )
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE rental_listings SET seen_last_at=? WHERE source_site='domimmo' AND source_id='0'",
        ("2026-08-12T10:01:00+00:00",),
    )
    con.commit()
    con.close()
    realestate_watch.mark_stale_not_seen(
        db, "2026-08-12T10:00:00+00:00", ["domimmo"], run_id="run-2"
    )
    con = sqlite3.connect(db)
    state = con.execute(
        "SELECT COUNT(*) FROM source_absence_state WHERE source_site='domimmo' AND source_id='0'"
    ).fetchone()[0]
    active = con.execute("SELECT is_active FROM rental_listings WHERE source_id='0'").fetchone()[0]
    con.close()
    assert state == 0
    assert active == 1


def test_scope_version_change_resets_only_that_sources_volume_baseline(tmp_path, monkeypatch):
    root = tmp_path / "data"
    project = root / "projects" / "reunion-immo-search"
    artifacts = project / "artifacts"
    artifacts.mkdir(parents=True)
    monkeypatch.setenv("IMMO_DATA_ROOT", str(root))

    module_path = Path(__file__).parents[1] / "scripts" / "immo_health_checks.py"
    spec = importlib.util.spec_from_file_location("immo_health_checks_scope_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    drops, resets = module.volume_drops(
        {"domimmo": 4, "ofim": 73, "zimo": 576, "leboncoin": 202},
        {"domimmo": 43, "ofim": 78, "zimo": 970, "leboncoin": 401},
        {},
    )

    assert drops == []
    assert set(resets) == {"domimmo", "ofim", "zimo", "leboncoin"}

    drops, resets = module.volume_drops(
        {"domimmo": 4, "ofim": 20, "zimo": 200},
        {"domimmo": 4, "ofim": 73, "zimo": 576},
        {"domimmo": 2, "ofim": 2, "zimo": 2},
    )
    assert resets == []
    assert drops == [
        {"source": "ofim", "avant": 73, "maintenant": 20, "chute_pct": 73},
        {"source": "zimo", "avant": 576, "maintenant": 200, "chute_pct": 65},
    ]

