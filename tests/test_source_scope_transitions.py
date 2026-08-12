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


def test_domimmo_complete_scope_deactivates_legacy_rows_immediately(tmp_path):
    db = tmp_path / "watch.db"
    _watch_db(db)

    changed = realestate_watch.mark_stale_not_seen(
        db,
        "2026-08-12T10:00:00+00:00",
        ["domimmo"],
        {"domimmo": 4},
    )

    con = sqlite3.connect(db)
    active = con.execute(
        "SELECT COUNT(*) FROM rental_listings WHERE source_site='domimmo' AND is_active=1"
    ).fetchone()[0]
    con.close()
    assert changed == {"domimmo": 39}
    assert active == 4


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
        {"domimmo": 4, "ofim": 73},
        {"domimmo": 43, "ofim": 78},
        {},
    )

    assert drops == []
    assert set(resets) == {"domimmo", "ofim"}

    drops, resets = module.volume_drops(
        {"domimmo": 4, "ofim": 20},
        {"domimmo": 4, "ofim": 73},
        {"domimmo": 2, "ofim": 2},
    )
    assert resets == []
    assert drops == [{"source": "ofim", "avant": 73, "maintenant": 20, "chute_pct": 73}]

