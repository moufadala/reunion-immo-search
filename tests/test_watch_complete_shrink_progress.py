from __future__ import annotations

from scripts import realestate_watch


def _complete_manifest() -> dict:
    return {
        "run_id": "adapter-run",
        "source": "ofim",
        "status": "complete",
        "attempted": True,
        "pages_attempted": 2,
        "pages_succeeded": 2,
        "fetched_items": 5,
        "parsed_items": 5,
        "unique_ids": 5,
        "normalized_items": 5,
        "rejected_items": 0,
        "rejected_items_by_reason": {},
        "inserted": 0,
        "updated": 0,
        "unchanged": 5,
        "withdrawn": 0,
        "reappeared": 0,
        "expected_count": 5,
        "dataset_id": None,
        "retries": 0,
        "truncation_signals": [],
        "error": None,
        "snapshot_proof": "all_target_routes_exhausted",
        "seen_ids": [f"id-{index}" for index in range(5)],
    }


def test_proven_complete_market_shrink_warns_but_does_not_deadlock_lifecycle():
    result = realestate_watch.RunnerResult(
        script="multi.py",
        ok=True,
        exit_code=0,
        stdout_path="",
        stderr_path="",
        parsed_summary={},
        source="ofim",
        source_manifest=_complete_manifest(),
    )

    manifest = realestate_watch.source_manifests_for_results(
        [result], run_id="adapter-run", active_before={"ofim": 100}
    )[0]

    assert manifest["status"] == "complete"
    assert manifest["truncation_signals"] == []
    assert manifest["coverage_warning"] == "below_previous_floor:5/100"
    assert realestate_watch.authoritative_withdrawal_sources([manifest]) == ["ofim"]
