from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rollback_public_app


def test_partial_install_failure_restores_every_original_child(tmp_path, monkeypatch):
    target, prepared, snapshot = tmp_path / "app", tmp_path / "prepared", tmp_path / "snapshot"
    target.mkdir()
    prepared.mkdir()
    for name in ("index.html", "listings.json"):
        (target / name).write_text("old-" + name, encoding="utf-8")
        (prepared / name).write_text("new-" + name, encoding="utf-8")

    real_replace = rollback_public_app.os.replace
    calls = 0

    def fail_during_second_install(src, dst):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected install failure")
        real_replace(src, dst)

    monkeypatch.setattr(rollback_public_app.os, "replace", fail_during_second_install)

    with pytest.raises(OSError, match="injected install failure"):
        rollback_public_app.transactional_child_swap(prepared, target, snapshot)

    assert sorted(p.name for p in target.iterdir()) == ["index.html", "listings.json"]
    assert all(p.read_text(encoding="utf-8").startswith("old-") for p in target.iterdir())
    assert sorted(p.name for p in prepared.iterdir()) == ["index.html", "listings.json"]
    assert not snapshot.exists()
