from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from src.source_manifest_bundle import CRITICAL_PORTALS


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_source_manifest_bundle.py"
SPEC = importlib.util.spec_from_file_location("build_source_manifest_bundle", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli
SPEC.loader.exec_module(cli)


def _complete(source: str) -> dict:
    return {
        "run_id": "run-1", "source": source, "status": "complete",
        "attempted": True, "pages_attempted": 1, "pages_succeeded": 1,
        "fetched_items": 1, "parsed_items": 1, "unique_ids": 1,
        "normalized_items": 1, "rejected_items": 0, "inserted": 0,
        "updated": 0, "unchanged": 1, "withdrawn": 0, "reappeared": 0,
        "expected_count": 1, "dataset_id": None, "retries": 0,
        "truncation_signals": [], "error": None,
        "seen_ids": [f"{source}:1"],
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_cli_merges_seloger_and_returns_zero_only_for_green_fourteen_source_gate(
    tmp_path: Path,
):
    base = _write(
        tmp_path / "base.json",
        {
            "run_id": "run-1",
            "sources": [
                _complete(source)
                for source in sorted(CRITICAL_PORTALS - {"seloger"})
            ],
        },
    )
    seloger = _write(tmp_path / "seloger.json", _complete("seloger"))
    output = tmp_path / "bundle.json"

    code = cli.main(
        ["--base", str(base), "--external", str(seloger), "--out", str(output)]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["gate"]["ok"] is True
    assert len(payload["sources"]) == 14


def test_cli_writes_failed_gate_and_returns_two_when_seloger_is_missing(
    tmp_path: Path,
):
    base = _write(
        tmp_path / "base.json",
        {
            "run_id": "run-1",
            "sources": [
                _complete(source)
                for source in sorted(CRITICAL_PORTALS - {"seloger"})
            ],
        },
    )
    output = tmp_path / "bundle.json"

    code = cli.main(["--base", str(base), "--out", str(output)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert code == 2
    assert payload["gate"]["missing_sources"] == ["seloger"]
