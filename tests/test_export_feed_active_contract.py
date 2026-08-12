from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

profils = types.ModuleType("profils")
profils.PROFILS = {}
profils.scorer = lambda listing, key: {"score": 0}
sys.modules["profils"] = profils
from export_feed import active_public_listings


def test_feed_listings_contains_only_active_rows() -> None:
    rows = [{"id": "active", "active": True}, {"id": "gone", "active": False}]
    assert active_public_listings(rows) == [{"id": "active", "active": True}]

