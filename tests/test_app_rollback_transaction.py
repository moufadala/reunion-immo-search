import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ROLLBACK = ROOT / "scripts" / "rollback_public_app.py"
DAILY = ROOT / "scripts" / "immo_daily_public_refresh.sh"


def _app(path: Path, marker: str) -> Path:
    path.mkdir()
    (path / "index.html").write_text(marker, encoding="utf-8")
    (path / "listings.json").write_text(
        json.dumps({"listings": [{"id": marker}]}), encoding="utf-8"
    )
    (path / "thumbs").mkdir()
    (path / "thumbs" / "photo.jpg").write_bytes((marker * 100).encode())
    return path


def test_apply_rollback_preserves_target_inode_and_hardlinks_backup_media(tmp_path):
    backup = _app(tmp_path / "app.pre-promote", "old")
    target = _app(tmp_path / "app", "new")
    target_inode = target.stat().st_ino
    former_media_inode = (target / "thumbs" / "photo.jpg").stat().st_ino
    report = tmp_path / "rollback.json"

    result = subprocess.run(
        [sys.executable, str(ROLLBACK), "--apply", "--backup", str(backup),
         "--target", str(target), "--json-out", str(report)],
        cwd=ROOT, text=True, capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    snapshot = Path(payload["target_snapshot"])
    assert target.stat().st_ino == target_inode
    assert (target / "thumbs" / "photo.jpg").stat().st_ino == (backup / "thumbs" / "photo.jpg").stat().st_ino
    assert (snapshot / "thumbs" / "photo.jpg").stat().st_ino == former_media_inode
    assert payload["transaction"] == "prepared_child_swap"
    assert (target / "index.html").read_text(encoding="utf-8") == "old"
    assert (snapshot / "index.html").read_text(encoding="utf-8") == "new"


def test_daily_failure_trap_uses_validated_transactional_rollback():
    source = DAILY.read_text(encoding="utf-8")
    trap = source[source.index("restore_on_failure()") : source.index("trap restore_on_failure EXIT")]

    assert 'rollback_public_app.py" --apply' in trap
    assert '--media-copy-mode hardlink' in trap
    assert 'rm -rf "$PROJECT/artifacts/app"' not in trap
    assert 'cp -a "$BACKUP_APP" "$PROJECT/artifacts/app"' not in trap
