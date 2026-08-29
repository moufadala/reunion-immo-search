from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "seloger_multi_page.py"
spec = importlib.util.spec_from_file_location("seloger_multi_page_checkpoint_mod", MOD_PATH)
assert spec is not None and spec.loader is not None
seloger = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = seloger
spec.loader.exec_module(seloger)


def card(i: int) -> dict:
    return {"id": f"id-{i}", "url": f"https://example.test/{i}", "prix": 900, "surface": 70}


def test_seloger_page_checkpoints_are_run_scoped_and_ordered(tmp_path, monkeypatch):
    monkeypatch.setenv("IMMO_REFRESH_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("IMMO_RUN_ID", "run-a")
    monkeypatch.setenv("IMMO_RESUME_COMPLETED", "1")
    seloger.save_page_checkpoint(1, [card(1), card(2)], reported_total=3, total_text="3 annonces")
    seloger.save_page_checkpoint(2, [card(3)], reported_total=3, new_count=1)
    # Foreign run must be ignored.
    foreign = tmp_path / "seloger_page_checkpoints" / "page-003.json"
    foreign.write_text(json.dumps({"run_id": "run-b", "page": 3, "cards": [card(4)]}), encoding="utf-8")

    loaded = seloger.load_page_checkpoints("run-a")
    assert [p["page"] for p in loaded] == [1, 2]
    assert [len(p["cards"]) for p in loaded] == [2, 1]


def test_seloger_complete_checkpoint_sequence_keeps_manifest_evaluable(tmp_path, monkeypatch):
    monkeypatch.setenv("IMMO_REFRESH_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("IMMO_RUN_ID", "run-a")
    monkeypatch.setenv("IMMO_RESUME_COMPLETED", "1")
    seloger.save_page_checkpoint(1, [card(i) for i in range(30)], reported_total=None)
    seloger.save_page_checkpoint(2, [card(30 + i) for i in range(12)], reported_total=None)
    loaded = seloger.load_page_checkpoints("run-a")
    page_sizes = [len(p["cards"]) for p in loaded]
    evidence = seloger.evaluate_seloger_collection(
        page_sizes=page_sizes,
        unique_ids=sum(page_sizes),
        reported_total=None,
        terminal_reason="short_page",
        pages_attempted=len(page_sizes),
        pages_succeeded=len(page_sizes),
        error=None,
        page_size=seloger.PAGE_SIZE,
    )
    assert evidence.complete is True
    assert evidence.page_sizes == (30, 12)
