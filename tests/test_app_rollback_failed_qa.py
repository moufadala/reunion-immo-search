import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ROLLBACK = ROOT / "scripts" / "rollback_public_app.py"


def _app(path: Path, marker: str) -> None:
    path.mkdir()
    (path / "index.html").write_text(marker, encoding="utf-8")
    (path / "listings.json").write_text(json.dumps({"listings": [{"id": marker}]}), encoding="utf-8")


def test_failed_pre_swap_qa_leaves_target_intact_and_removes_prepared_tree(tmp_path):
    backup, target = tmp_path / "app.pre-promote-test", tmp_path / "app"
    _app(backup, "old")
    _app(target, "new")
    report = tmp_path / "rollback.json"

    result = subprocess.run(
        [sys.executable, str(ROLLBACK), "--apply", "--backup", str(backup),
         "--target", str(target), "--json-out", str(report),
         "--qa-cmd", f'"{sys.executable}" -c "raise SystemExit(1)"'],
        cwd=ROOT, text=True, capture_output=True,
    )

    assert result.returncode == 2
    assert (target / "index.html").read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob("app.rollback-prepared-*"))
    assert json.loads(report.read_text(encoding="utf-8"))["transaction"] == "aborted_before_swap"
