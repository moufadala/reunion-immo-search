from __future__ import annotations

import os
from pathlib import Path

from scripts import realestate_watch


ROOT = Path(__file__).resolve().parents[1]


def test_daily_pipeline_exports_the_canonical_timestamp_as_run_id():
    script = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(
        encoding="utf-8"
    )

    stamp = script.index('STAMP="${IMMO_REFRESH_STAMP:-')
    exported = script.index('export IMMO_RUN_ID="$STAMP"')
    refresh = script.index("run_step realestate_refresh")

    assert stamp < exported < refresh


def test_watcher_preserves_a_canonical_run_id_from_its_parent(monkeypatch, tmp_path):
    captured: list[str] = []

    class Completed:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured.append(kwargs["env"]["IMMO_RUN_ID"])
        stdout = kwargs["stdout"]
        stdout.write('{"ok": true, "by_source": {"ofim": 1}}\n')
        stdout.flush()
        return Completed()

    monkeypatch.setattr(realestate_watch, "SOURCE_JOBS", [
        {"source": "ofim", "script": ROOT / "fake.py", "timeout": 10, "args": []}
    ])
    monkeypatch.setattr(realestate_watch.subprocess, "run", fake_run)
    monkeypatch.setenv("IMMO_RUN_ID", "20260820T120000Z")

    run_dir = tmp_path / "immo_public_refresh" / "realestate_watch"
    run_dir.mkdir(parents=True)
    realestate_watch.run_scrapers(tmp_path / "watch.db", run_dir)

    assert captured == ["20260820T120000Z"]
    assert os.environ["IMMO_RUN_ID"] == "20260820T120000Z"
