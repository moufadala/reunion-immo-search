from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "export_feed.py"
SPEC = importlib.util.spec_from_file_location("export_feed", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
export_feed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export_feed)


def base_listing(id_: str, source: str, title: str = "Maison à Bagatelle", rent: int = 940, surface: float | None = 82.0, rooms: int = 4) -> dict:
    return {
        "id": id_,
        "source": source,
        "title": title,
        "commune": "Sainte-Suzanne",
        "rent": rent,
        "surface": surface,
        "rooms": rooms,
        "image": "/thumbs/x.jpg" if source == "leboncoin" else None,
        "description": "description source longue et utile " * (3 if source == "leboncoin" else 1),
        "active": True,
    }


def test_public_rule_excludes_unknown_and_under_65_surface() -> None:
    assert export_feed.public_rule_violation({"rent": 900, "surface": None}) == "surface_inconnue"
    assert export_feed.public_rule_violation({"rent": 900, "surface": 64.99}) == "surface_inf_65"
    assert export_feed.public_rule_violation({"rent": 900, "surface": 65}) is None


def test_canonical_public_feed_hides_only_strong_cross_source_duplicates() -> None:
    obvious_a = base_listing("leboncoin:1", "leboncoin")
    obvious_b = base_listing("zimo:1", "zimo")
    near_match = base_listing("bienici:1", "bienici", title="Maison Bagatelle proche école")

    visible, meta = export_feed.canonical_public_feed([obvious_a, obvious_b, near_match])

    assert meta["groups"] == 1
    assert meta["hidden_rows"] == 1
    assert len(visible) == 2
    assert {x["id"] for x in visible} == {"zimo:1", "bienici:1"}
    assert obvious_a["display_canonical"] is False
    assert obvious_b["display_canonical"] is True
    assert near_match["display_canonical"] is True


def test_canonical_public_feed_keeps_same_signature_same_source_visible() -> None:
    a = base_listing("zimo:1", "zimo")
    b = base_listing("zimo:2", "zimo")

    visible, meta = export_feed.canonical_public_feed([a, b])

    assert meta["groups"] == 0
    assert meta["hidden_rows"] == 0
    assert len(visible) == 2
