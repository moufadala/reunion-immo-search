from __future__ import annotations

import json

from scripts import realestate_watch


def test_watcher_recognizes_a_single_source_manifest_printed_by_bienici(tmp_path):
    manifest = {
        "run_id": "run-1",
        "source": "bienici",
        "status": "complete",
        "attempted": True,
        "pages_attempted": 1,
        "pages_succeeded": 1,
        "fetched_items": 1,
        "parsed_items": 1,
        "unique_ids": 1,
        "normalized_items": 1,
        "rejected_items": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 1,
        "withdrawn": 0,
        "reappeared": 0,
        "expected_count": 1,
        "dataset_id": None,
        "retries": 0,
        "truncation_signals": [],
        "error": None,
        "seen_ids": ["b-1"],
    }
    stdout = tmp_path / "bienici.stdout"
    stdout.write_text(json.dumps(manifest), encoding="utf-8")

    parsed = realestate_watch.parse_scraper_stdout(stdout)

    assert parsed["source_manifests"] == {"bienici": manifest}


def test_bienici_direct_manifest_is_a_successful_runner_and_source_gate_input(tmp_path):
    manifest = {
        "run_id": "run-1", "source": "bienici", "status": "complete",
        "attempted": True, "pages_attempted": 1, "pages_succeeded": 1,
        "fetched_items": 1, "parsed_items": 1, "unique_ids": 1,
        "normalized_items": 1, "rejected_items": 0, "inserted": 0,
        "updated": 0, "unchanged": 1, "withdrawn": 0, "reappeared": 0,
        "expected_count": 1, "dataset_id": None, "retries": 0,
        "truncation_signals": [], "error": None, "seen_ids": ["b-1"],
    }
    stdout = tmp_path / "bienici.stdout"
    stdout.write_text(json.dumps(manifest), encoding="utf-8")

    parsed = realestate_watch.parse_scraper_stdout(stdout)
    runner_ok = realestate_watch._source_result_ok(parsed, "bienici", 0)
    result = realestate_watch.RunnerResult(
        script="bienici_rental_scraper", ok=runner_ok, exit_code=0,
        stdout_path=str(stdout), stderr_path="", parsed_summary=parsed,
        source="bienici", source_manifest=manifest,
    )
    manifests = realestate_watch.source_manifests_for_results(
        [result], run_id="run-1"
    )

    assert runner_ok is True
    assert realestate_watch.evaluate_source_gate(
        [result], source_manifests=manifests
    )["ok"] is True
