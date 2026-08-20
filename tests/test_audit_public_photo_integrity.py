import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "audit_public_photo_integrity.py"
JPEG = b"\xff\xd8\xff"


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    app = tmp_path / "app"
    (app / "thumbs").mkdir(parents=True)
    (app / "thumbs" / "a.jpg").write_bytes(JPEG + b"same")
    (app / "thumbs" / "b.jpg").write_bytes(JPEG + b"same")
    feed = app / "feed.json"
    feed.write_text(
        json.dumps(
            {
                "listings": [
                    {
                        "id": "portal:1",
                        "image": "/thumbs/a.jpg",
                        "images": ["/thumbs/a.jpg", "/thumbs/b.jpg"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return app, feed


def test_cli_is_a_blocking_gate_and_reports_baseline_to_canonical_target(tmp_path: Path) -> None:
    app, feed = _fixture(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--app", str(app), "--feed", str(feed)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert report["baseline"]["galleries_with_content_duplicates"] == 1
    assert report["canonical_target"]["ok"] is True
    assert report["canonical_target"]["galleries_with_content_duplicates"] == 0
    assert report["canonicalization"]["listings_changed"] == 1
    assert report["canonicalization"]["removed_content_duplicates"] == 1


def test_cli_passes_after_feed_gallery_is_already_canonical(tmp_path: Path) -> None:
    app, feed = _fixture(tmp_path)
    payload = json.loads(feed.read_text(encoding="utf-8"))
    payload["listings"][0]["images"] = ["/thumbs/a.jpg"]
    feed.write_text(json.dumps(payload), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--app", str(app), "--feed", str(feed)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    assert json.loads(proc.stdout)["ok"] is True


def test_cli_blocks_an_existing_file_without_image_content(tmp_path: Path) -> None:
    app, feed = _fixture(tmp_path)
    (app / "thumbs" / "a.jpg").write_bytes(b"")
    payload = json.loads(feed.read_text(encoding="utf-8"))
    payload["listings"][0]["images"] = ["/thumbs/a.jpg"]
    feed.write_text(json.dumps(payload), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--app", str(app), "--feed", str(feed)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["baseline"]["invalid_image_files"] == 1
    assert any(
        row["type"] == "invalid_local_image" for row in report["baseline"]["violations"]
    )
