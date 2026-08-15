from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from artifact_retention import run


def test_retention_bounds_pre_rollback_snapshots(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    snapshots = []
    for index in range(3):
        snapshot = artifacts / f"app.pre-rollback-20260815T00000{index}Z"
        snapshot.mkdir()
        (snapshot / "index.html").write_text(str(index), encoding="utf-8")
        snapshots.append(snapshot)

    report = run(artifacts, apply=True, keep_pre_promote=1)

    assert report["after"]["app_pre_rollback_count"] == 1
    assert snapshots[-1].exists()
    assert not snapshots[0].exists()
    assert not snapshots[1].exists()
