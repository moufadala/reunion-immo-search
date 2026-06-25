#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "import_seloger_multipage.py"
spec = importlib.util.spec_from_file_location("import_seloger_multipage", SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def active_rows(con: sqlite3.Connection, source: str) -> dict[str, int]:
    return dict(con.execute("SELECT source_id, is_active FROM rental_listings WHERE source_site=?", (source,)).fetchall())


with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "watch.db"
    con = sqlite3.connect(db)
    mod.init_db(con)
    now = "2026-06-25T00:00:00+00:00"
    for sid in [str(i) for i in range(7)]:
        con.execute(
            """
            INSERT INTO rental_listings(source_site, source_id, url, seen_first_at, seen_last_at, is_active)
            VALUES ('seloger', ?, ?, ?, ?, 1)
            """,
            (sid, f"https://example.test/{sid}", now, now),
        )
    con.execute(
        """
        INSERT INTO rental_listings(source_site, source_id, url, seen_first_at, seen_last_at, is_active)
        VALUES ('zimo', 'z1', 'https://example.test/z1', ?, ?, 1)
        """,
        (now, now),
    )
    con.commit()

    changed = mod.mark_seloger_inactive_not_seen(con, {"0", "1", "2", "3", "4"}, batch_size=3)
    con.commit()

    rows = active_rows(con, "seloger")
    zimo_active = con.execute("SELECT is_active FROM rental_listings WHERE source_site='zimo' AND source_id='z1'").fetchone()[0]
    con.close()

    assert changed == 2, changed
    assert rows == {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1, "5": 0, "6": 0}, rows
    assert zimo_active == 1

    raw_slug = {
        "id": "slug1",
        "url": "https://www.seloger.com/annonces/locations/maison/petite-ile-974/270951629.htm",
        "prix": 1700,
        "surface": 158.0,
        "nb_pieces": 5,
        "type_bien": "Maison",
    }
    item_slug = mod.normalize_item(raw_slug, Path(td) / "seloger.json")
    assert item_slug["city"] == "Petite-Île", item_slug
    assert "Petite-Île" in item_slug["title"], item_slug["title"]
    assert "commune: Petite-Île" in item_slug["description"], item_slug["description"]

    raw_field = {
        "id": "field1",
        "url": "https://www.seloger.com/field1/detail.htm",
        "ville": "Bras Panon",
        "prix": 1290,
        "surface": 118,
        "type_bien": "Maison",
    }
    item_field = mod.normalize_item(raw_field, Path(td) / "seloger.json")
    assert item_field["city"] == "Bras-Panon", item_field

    raw_opaque = {"id": "opaque1", "url": "https://www.seloger.com/270154253/detail.htm", "prix": 960}
    item_opaque = mod.normalize_item(raw_opaque, Path(td) / "seloger.json")
    assert item_opaque["city"] is None, item_opaque

print("IMPORT_SELOGER_MULTIPAGE_AUDIT PASS")
