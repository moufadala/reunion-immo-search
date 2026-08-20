from pathlib import Path

from src.photo_gallery import audit_public_galleries
JPEG = b"\xff\xd8\xff"


def test_public_audits_can_share_file_hash_cache_across_baseline_and_target(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "thumbs").mkdir(parents=True)
    (app / "thumbs" / "a.jpg").write_bytes(JPEG + b"a")
    (app / "thumbs" / "b.jpg").write_bytes(JPEG + b"b")
    listings = [
        {
            "id": "portal:1",
            "images": ["/thumbs/a.jpg", "/thumbs/b.jpg"],
        }
    ]
    cache: dict[Path, str] = {}

    first = audit_public_galleries(listings, app_root=app, hash_cache=cache)
    second = audit_public_galleries(listings, app_root=app, hash_cache=cache)

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(cache) == 2
