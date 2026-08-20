import hashlib
from pathlib import Path

import src.photo_gallery as photo_gallery
from src.photo_gallery import (
    audit_public_galleries,
    canonicalize_gallery,
    canonicalize_listing_gallery,
)
JPEG = b"\xff\xd8\xff"


def _write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return "/" + path.relative_to(path.parents[1]).as_posix()


def test_gallery_keeps_first_url_and_first_binary_without_deleting_files(tmp_path: Path) -> None:
    app = tmp_path / "app"
    first = app / "thumbs" / "first.jpg"
    binary_twin = app / "thumbs" / "same-pixels-different-name.jpg"
    other = app / "thumbs" / "other.jpg"
    first.parent.mkdir(parents=True)
    first.write_bytes(JPEG + b"same-pixels")
    binary_twin.write_bytes(JPEG + b"same-pixels")
    other.write_bytes(JPEG + b"other-pixels")

    gallery, report = canonicalize_gallery(
        [
            "/thumbs/first.jpg",
            "/thumbs/first.jpg",
            "/thumbs/same-pixels-different-name.jpg",
            "/thumbs/other.jpg",
        ],
        app_root=app,
    )

    assert gallery == ["/thumbs/first.jpg", "/thumbs/other.jpg"]
    assert report["duplicate_urls"] == 1
    assert report["duplicate_content"] == 1
    assert report["kept"] == 2
    assert first.read_bytes() == JPEG + b"same-pixels"
    assert binary_twin.read_bytes() == JPEG + b"same-pixels"
    assert other.read_bytes() == JPEG + b"other-pixels"


def test_missing_or_unsafe_files_are_kept_and_never_hash_collide(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()

    gallery, report = canonicalize_gallery(
        ["/thumbs/missing-a.jpg", "/thumbs/missing-b.jpg", "/../outside.jpg"],
        app_root=app,
    )

    assert gallery == ["/thumbs/missing-a.jpg", "/thumbs/missing-b.jpg", "/../outside.jpg"]
    assert report["missing_files"] == 2
    assert report["unsafe_paths"] == 1
    assert report["duplicate_content"] == 0


def test_listing_primary_is_first_and_unrelated_fields_are_preserved(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "thumbs").mkdir(parents=True)
    (app / "thumbs" / "cover.jpg").write_bytes(JPEG + b"cover")
    (app / "thumbs" / "second.jpg").write_bytes(JPEG + b"second")
    listing = {
        "id": "portal:1",
        "image": "/thumbs/cover.jpg",
        "images": ["/thumbs/second.jpg", "/thumbs/cover.jpg"],
        "description": "must survive",
        "custom": {"proof": True},
    }

    canonical, report = canonicalize_listing_gallery(listing, app_root=app)

    assert canonical["image"] == "/thumbs/cover.jpg"
    assert canonical["images"] == ["/thumbs/cover.jpg", "/thumbs/second.jpg"]
    assert canonical["description"] == "must survive"
    assert canonical["custom"] == {"proof": True}
    assert listing["images"] == ["/thumbs/second.jpg", "/thumbs/cover.jpg"]
    assert report["duplicate_urls"] == 0


def test_public_gallery_audit_reports_repeated_hash_then_zero_after_canonicalization(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "thumbs").mkdir(parents=True)
    (app / "thumbs" / "a.jpg").write_bytes(JPEG + b"same")
    (app / "thumbs" / "b.jpg").write_bytes(JPEG + b"same")
    digest = hashlib.sha256(JPEG + b"same").hexdigest()
    listing = {
        "id": "portal:1",
        "image": "/thumbs/a.jpg",
        "images": ["/thumbs/a.jpg", "/thumbs/b.jpg"],
    }

    baseline = audit_public_galleries([listing], app_root=app)
    canonical, _ = canonicalize_listing_gallery(listing, app_root=app)
    target = audit_public_galleries([canonical], app_root=app)

    assert baseline["ok"] is False
    assert baseline["galleries_with_content_duplicates"] == 1
    assert baseline["violations"][0]["repeated_content"][0]["sha256"] == digest
    assert baseline["violations"][0]["repeated_content"][0]["paths"] == [
        "/thumbs/a.jpg",
        "/thumbs/b.jpg",
    ]
    assert target["ok"] is True
    assert target["galleries_with_content_duplicates"] == 0


def test_public_gallery_audit_blocks_a_missing_local_file(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()

    report = audit_public_galleries(
        [{"id": "portal:missing", "images": ["/thumbs/missing.jpg"]}],
        app_root=app,
    )

    assert report["ok"] is False
    assert report["missing_files"] == 1
    assert {row["type"] for row in report["violations"]} == {"missing_local_file"}
    assert report["violations"][0]["id"] == "portal:missing"


def test_public_gallery_audit_blocks_an_unsafe_local_path(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()

    report = audit_public_galleries(
        [{"id": "portal:unsafe", "images": ["/../outside.jpg"]}],
        app_root=app,
    )

    assert report["ok"] is False
    assert report["unsafe_paths"] == 1
    assert {row["type"] for row in report["violations"]} == {"unsafe_local_path"}


def test_public_gallery_audit_blocks_an_unreadable_local_file(tmp_path: Path, monkeypatch) -> None:
    app = tmp_path / "app"
    photo = app / "thumbs" / "unreadable.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(JPEG + b"present-but-unreadable")

    def fail_to_read(_path, _cache):
        raise OSError("permission denied")

    monkeypatch.setattr(photo_gallery, "_sha256", fail_to_read)
    report = audit_public_galleries(
        [{"id": "portal:unreadable", "images": ["/thumbs/unreadable.jpg"]}],
        app_root=app,
    )

    assert report["ok"] is False
    assert report["unreadable_files"] == 1
    assert {row["type"] for row in report["violations"]} == {"unreadable_local_file"}


def test_public_gallery_audit_blocks_empty_and_non_image_local_files(tmp_path: Path) -> None:
    app = tmp_path / "app"
    thumbs = app / "thumbs"
    thumbs.mkdir(parents=True)
    (thumbs / "empty.jpg").write_bytes(b"")
    (thumbs / "text.jpg").write_bytes(b"this is not an image")

    report = audit_public_galleries(
        [
            {"id": "portal:empty", "images": ["/thumbs/empty.jpg"]},
            {"id": "portal:text", "images": ["/thumbs/text.jpg"]},
        ],
        app_root=app,
    )

    assert report["ok"] is False
    assert report["invalid_image_files"] == 2
    violations = [row for row in report["violations"] if row["type"] == "invalid_local_image"]
    assert {row["id"] for row in violations} == {"portal:empty", "portal:text"}


def test_public_gallery_audit_blocks_external_only_gallery_as_uncached(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()

    report = audit_public_galleries(
        [{"id": "portal:external", "images": ["https://cdn.example/photo.jpg"]}],
        app_root=app,
    )

    assert report["ok"] is False
    assert report["external_urls"] == 1
    assert {row["type"] for row in report["violations"]} == {"external_only_gallery"}
    assert report["violations"][0]["id"] == "portal:external"
