from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "realestate_watch.py"
spec = importlib.util.spec_from_file_location("realestate_watch_resume_mod", MOD_PATH)
assert spec is not None and spec.loader is not None
realestate_watch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = realestate_watch
spec.loader.exec_module(realestate_watch)


def write_status(run_dir: Path, source: str, run_id: str) -> None:
    script = Path("multi.py")
    name = f"{script.stem}__{source}"
    parsed = {
        "json_ok": True,
        "source_status": {source: {"ok": True, "count": 2}},
        "source_manifests": {
            source: {
                "run_id": run_id,
                "source": source,
                "status": "complete",
                "attempted": True,
                "pages_attempted": 1,
                "pages_succeeded": 1,
                "fetched_items": 2,
                "parsed_items": 2,
                "unique_ids": 2,
                "normalized_items": 2,
                "rejected_items": 0,
                "rejected_items_by_reason": {},
                "unparsed_items_by_reason": {},
                "pre_unique_rejections_by_reason": {},
                "inserted": 1,
                "updated": 1,
                "unchanged": 0,
                "withdrawn": 0,
                "reappeared": 0,
                "expected_count": None,
                "previous_count": None,
                "dataset_id": None,
                "retries": 0,
                "snapshot_proof": "all_target_routes_exhausted",
                "seen_ids": ["a", "b"],
                "truncation_signals": [],
                "error": None,
            }
        },
    }
    (run_dir / f"{name}.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (run_dir / f"{name}.stderr").write_text("", encoding="utf-8")
    (run_dir / f"{name}.status.json").write_text(
        json.dumps(
            {
                "source": source,
                "script": str(script),
                "cmd": ["python", str(script)],
                "started_at": "2026-08-29T00:00:00+00:00",
                "duration_sec": 1.2,
                "timeout_sec": 10,
                "budget_sec": 99,
                "ok": True,
                "exit_code": 0,
                "stdout_path": str(run_dir / f"{name}.json"),
                "stderr_path": str(run_dir / f"{name}.stderr"),
                "parsed_summary": parsed,
            }
        ),
        encoding="utf-8",
    )


def test_resumed_result_requires_same_run_id(tmp_path, monkeypatch):
    write_status(tmp_path, "bienici", "run-a")
    monkeypatch.setenv("IMMO_RESUME_COMPLETED", "1")
    assert realestate_watch._resumed_result("bienici", Path("multi.py"), tmp_path, "run-a").ok is True
    assert realestate_watch._resumed_result("bienici", Path("multi.py"), tmp_path, "run-b") is None


def test_run_scrapers_reuses_successful_checkpoints_without_subprocess(tmp_path, monkeypatch):
    for source in ("bienici", "ofim"):
        write_status(tmp_path, source, "same-run")
    monkeypatch.setenv("IMMO_RESUME_COMPLETED", "1")
    monkeypatch.setenv("IMMO_RUN_ID", "same-run")
    monkeypatch.setattr(realestate_watch, "SOURCE_JOBS", [
        {"source": "bienici", "script": Path("multi.py"), "timeout": 10, "args": []},
        {"source": "ofim", "script": Path("multi.py"), "timeout": 10, "args": []},
    ])

    def forbidden(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called for resumed sources")

    monkeypatch.setattr(realestate_watch.subprocess, "run", forbidden)
    results = realestate_watch.run_scrapers(tmp_path / "db.sqlite", tmp_path)
    assert [r.source for r in results] == ["bienici", "ofim"]
    assert all(r.ok for r in results)
