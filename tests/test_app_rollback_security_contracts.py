import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rollback_public_app


ROLLBACK = ROOT / "scripts" / "rollback_public_app.py"
DAILY = ROOT / "scripts" / "immo_daily_public_refresh.sh"


def _app(path: Path, marker: str) -> Path:
    path.mkdir()
    (path / "index.html").write_text(marker, encoding="utf-8")
    (path / "listings.json").write_text(
        json.dumps({"listings": [{"id": marker}]}), encoding="utf-8"
    )
    (path / "thumbs").mkdir()
    (path / "thumbs" / "photo.jpg").write_bytes(marker.encode())
    return path


def test_apply_normalizes_public_read_permissions(tmp_path):
    backup = _app(tmp_path / "app.pre-promote-test", "old")
    target = _app(tmp_path / "app", "new")
    for child in backup.rglob("*"):
        child.chmod(0o700 if child.is_dir() else 0o600)

    result = subprocess.run(
        [sys.executable, str(ROLLBACK), "--apply", "--backup", str(backup),
         "--target", str(target)],
        cwd=ROOT, text=True, capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert (target / "index.html").stat().st_mode & stat.S_IROTH
    assert (target / "thumbs").stat().st_mode & stat.S_IXOTH


def test_failed_post_swap_validation_restores_original_target(tmp_path):
    target = _app(tmp_path / "app", "old")
    prepared = _app(tmp_path / "prepared", "new")
    snapshot = tmp_path / "snapshot"

    with pytest.raises(RuntimeError, match="post-swap validation failed"):
        rollback_public_app.transactional_child_swap(
            prepared,
            target,
            snapshot,
            validator=lambda _path: {"ok": False, "missing": ["injected"]},
        )

    assert (target / "index.html").read_text(encoding="utf-8") == "old"
    assert (prepared / "index.html").read_text(encoding="utf-8") == "new"
    assert not snapshot.exists()


def test_retention_finishes_before_global_transaction_backups_exist():
    source = DAILY.read_text(encoding="utf-8")
    retention = source.index("run_step pre_promote_artifact_retention")
    global_begin = source.index("run_step global_publication_begin")
    assert retention < global_begin


def test_backup_with_symlink_is_rejected(tmp_path):
    backup = _app(tmp_path / "app.pre-promote-test", "old")
    external = tmp_path / "private.txt"
    external.write_text("private", encoding="utf-8")
    link = backup / "exposed.txt"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    check = rollback_public_app.validate_app(backup)
    assert check["ok"] is False
    assert any(str(item).startswith("unsafe_symlink:") for item in check["missing"])
