from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_feed.py"


def test_candidate_export_never_reads_photos_or_listings_from_live_app(
    tmp_path: Path, monkeypatch
) -> None:
    candidate = tmp_path / "candidate"
    live = tmp_path / "live"
    candidate.mkdir()
    live.mkdir()
    (live / "photos_manifest.json").write_text('{"live": true}', encoding="utf-8")
    (live / "listings.json").write_text('{"live": true}', encoding="utf-8")
    (live / "thumb.jpg").write_bytes(b"live")
    monkeypatch.setenv("IMMO_APP_PATH", str(candidate))
    monkeypatch.setenv("IMMO_FEED_OUT", str(candidate / "feed.json"))

    spec = importlib.util.spec_from_file_location("export_feed_candidate_boundary", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.APP_ROOT == candidate.resolve()
    assert module.app_file("photos_manifest.json") == candidate / "photos_manifest.json"
    assert module.app_file("listings.json") == candidate / "listings.json"
    assert module.local_media_exists("/thumb.jpg") is False
    source = SCRIPT.read_text(encoding="utf-8")
    photo_boundary = source[source.index("# --- PHOTOS") : source.index("    dist = {}")]
    assert "ROOT + '/artifacts/app" not in photo_boundary
    assert "os.path.join(ROOT, 'artifacts/app'" not in photo_boundary
