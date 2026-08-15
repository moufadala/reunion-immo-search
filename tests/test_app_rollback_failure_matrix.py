from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rollback_public_app


def _tree(path: Path, prefix: str) -> Path:
    path.mkdir()
    for name in ("index.html", "listings.json"):
        (path / name).write_text(f"{prefix}-{name}", encoding="utf-8")
    return path


def test_partial_original_move_failure_restores_target(tmp_path, monkeypatch):
    target = _tree(tmp_path / "app", "old")
    prepared = _tree(tmp_path / "prepared", "new")
    snapshot = tmp_path / "snapshot"
    real_replace = rollback_public_app.os.replace
    calls = 0

    def fail_second_move(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected original move failure")
        real_replace(src, dst)

    monkeypatch.setattr(rollback_public_app.os, "replace", fail_second_move)
    with pytest.raises(OSError, match="injected original move failure"):
        rollback_public_app.transactional_child_swap(prepared, target, snapshot)

    assert sorted(child.name for child in target.iterdir()) == ["index.html", "listings.json"]
    assert all(child.read_text(encoding="utf-8").startswith("old-") for child in target.iterdir())
    assert not snapshot.exists()


def test_snapshot_collision_changes_nothing(tmp_path):
    target = _tree(tmp_path / "app", "old")
    prepared = _tree(tmp_path / "prepared", "new")
    snapshot = _tree(tmp_path / "snapshot", "previous")

    with pytest.raises(FileExistsError):
        rollback_public_app.transactional_child_swap(prepared, target, snapshot)

    assert (target / "index.html").read_text(encoding="utf-8") == "old-index.html"
    assert (prepared / "index.html").read_text(encoding="utf-8") == "new-index.html"
    assert (snapshot / "index.html").read_text(encoding="utf-8") == "previous-index.html"


def test_failed_preparation_copy_removes_partial_tree(tmp_path, monkeypatch):
    source = _tree(tmp_path / "backup", "old")
    destination = tmp_path / "app.rollback-prepared"

    def partial_copy(_src, dst, **_kwargs):
        dst.mkdir()
        (dst / "partial.jpg").write_bytes(b"partial")
        raise OSError("injected copy failure")

    monkeypatch.setattr(rollback_public_app, "copytree_media_aware", partial_copy)
    with pytest.raises(OSError, match="injected copy failure"):
        rollback_public_app.copy_tree(source, destination, media_mode="hardlink")

    assert not destination.exists()
