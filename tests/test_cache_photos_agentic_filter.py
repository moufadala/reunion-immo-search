from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cache_photos.py"


def test_cache_photos_supports_agentic_source_filter_and_limit():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "PHOTO_DOWNLOAD_SOURCE_FILTER" in source
    assert "PHOTO_DOWNLOAD_LIMIT" in source
    assert "keep rebuilding the full" in source
    assert "str(r['source_site']).lower() not in DOWNLOAD_SOURCE_FILTER" in source
    assert "len(a_chercher) >= DOWNLOAD_LIMIT" in source
