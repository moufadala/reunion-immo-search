from __future__ import annotations

from dataclasses import dataclass

from scripts import realestate_multi_sources_scraper as multi
from scripts import realestate_watch


@dataclass
class FakeListing:
    source_id: str


def _runner(source: str, parsed: dict, ok: bool = True) -> realestate_watch.RunnerResult:
    return realestate_watch.RunnerResult(
        script=f"{source}.py", ok=ok, exit_code=0 if ok else 1,
        stdout_path="", stderr_path="", parsed_summary=parsed, source=source,
        duration_sec=1.0, timeout_sec=300, status_path=f"/{source}.status.json",
    )


def test_adapter_manifest_without_runtime_exhaustion_proof_stays_partial():
    item = multi.build_source_manifest(
        source="zimo", run_id="run-1",
        listings=[FakeListing("a"), FakeListing("b")],
        event_statuses=["seen", "changed"],
        source_status={"ok": True, "count": 2},
        fetch_log=[{"ok": True}, {"ok": True}],
    )
    assert item["status"] == "partial"
    assert item["pages_attempted"] == 2
    assert item["pages_succeeded"] == 2
    assert item["unique_ids"] == 2
    assert item["updated"] == 1
    assert item["unchanged"] == 1
    assert "full_snapshot_unproven" in item["truncation_signals"]
    assert "bounded_pagination" not in item["truncation_signals"]
    assert item["seen_ids"] == ["a", "b"]


def test_zero_page_evidence_can_never_be_complete():
    item = multi.build_source_manifest(
        source="ofim", run_id="run-1", listings=[FakeListing("a")],
        event_statuses=["seen"], source_status={"ok": True, "count": 1},
        fetch_log=[],
    )
    assert item["status"] == "partial"
    assert "no_page_evidence" in item["truncation_signals"]
    assert "full_snapshot_unproven" in item["truncation_signals"]


def test_source_below_its_result_cap_stays_partial_without_exhaustion_proof():
    item = multi.build_source_manifest(
        source="ofim", run_id="run-1", listings=[FakeListing("a")],
        event_statuses=["seen"], source_status={"ok": True, "count": 1},
        fetch_log=[{"ok": True}],
    )
    assert item["status"] == "partial"
    assert "result_cap_reached" not in " ".join(item["truncation_signals"])
    assert "full_snapshot_unproven" in item["truncation_signals"]


def test_leboncoin_actor_non_saturated_with_both_cities_proves_one_logical_snapshot():
    items = [
        {"list_id": 1, "location": {"city": "Saint-Denis"}},
        {"list_id": 2, "location": {"city": "Sainte-Marie"}},
    ]
    meta = multi._leboncoin_runtime_meta(
        items, dataset_id="ds", mode="actor_run", max_items=700,
    )
    assert meta["full_snapshot_proof"] is True
    assert meta["pages_attempted"] == 1
    assert meta["pages_succeeded"] == 1
    assert meta["truncation_signals"] == []


def test_leboncoin_dataset_reuse_and_saturated_actor_are_partial():
    items = [
        {"list_id": i, "location": {"city": "Saint-Denis" if i % 2 else "Sainte-Marie"}}
        for i in range(700)
    ]
    reused = multi._leboncoin_runtime_meta(items[:2], dataset_id="ds", mode="dataset", max_items=700)
    saturated = multi._leboncoin_runtime_meta(items, dataset_id="ds", mode="actor_run", max_items=700)
    assert reused["full_snapshot_proof"] is False
    assert "dataset_reuse_unverified" in reused["truncation_signals"]
    assert saturated["full_snapshot_proof"] is False
    assert "dataset_limit_reached:700" in saturated["truncation_signals"]


def test_adapter_manifest_keeps_apify_dataset_identity_and_rejection_counts():
    item = multi.build_source_manifest(
        source="leboncoin", run_id="run-1",
        listings=[FakeListing("a"), FakeListing("b")],
        event_statuses=["new", "seen"],
        source_status={"ok": True, "count": 2}, fetch_log=[],
        runtime_meta={
            "raw_items": 3, "unique_ids": 3, "dataset_id": "dataset-123",
            "pages_attempted": 4, "pages_succeeded": 4,
            "truncation_signals": ["page_cap:1"],
        },
    )
    assert item["status"] == "partial"
    assert item["dataset_id"] == "dataset-123"
    assert item["fetched_items"] == 3
    assert item["normalized_items"] == 2
    assert item["rejected_items"] == 1


def test_watcher_downgrades_legacy_success_without_manifest_to_partial():
    result = _runner(
        "bienici",
        {"json_ok": True, "by_source": {"bienici": 90},
         "source_status": {"bienici": {"ok": True, "count": 90}}},
    )
    manifests = realestate_watch.source_manifests_for_results(
        [result], run_id="run-1", active_before={"bienici": 100}
    )
    assert manifests[0]["status"] == "partial"
    assert manifests[0]["expected_count"] == 100
    assert "legacy_manifest_missing" in manifests[0]["truncation_signals"]


def test_watcher_rejects_a_child_manifest_from_another_run_instead_of_relabelling_it():
    child = {
        "run_id": "child-run", "source": "ofim", "status": "complete",
        "attempted": True, "pages_attempted": 1, "pages_succeeded": 1,
        "fetched_items": 1, "parsed_items": 1, "unique_ids": 1,
        "normalized_items": 1, "rejected_items": 0,
        "inserted": 0, "updated": 0, "unchanged": 1,
        "withdrawn": 0, "reappeared": 0, "expected_count": 1,
        "dataset_id": None, "retries": 0, "truncation_signals": [],
        "error": None, "seen_ids": ["ofim-1"],
    }
    result = _runner("ofim", {"json_ok": True})
    result.source_manifest = child

    manifests = realestate_watch.source_manifests_for_results(
        [result], run_id="pipeline-run"
    )
    gate = realestate_watch.evaluate_source_gate(
        [result], source_manifests=manifests
    )

    assert manifests[0]["run_id"] == "child-run"
    assert manifests[0]["status"] == "failed"
    assert manifests[0]["withdrawn"] == 0
    assert "run_id mismatch" in manifests[0]["error"]
    assert gate["ok"] is False


def test_only_complete_manifest_can_mark_missing_rows_inactive():
    complete = {
        "run_id": "run-1", "source": "ofim", "status": "complete",
        "attempted": True, "pages_attempted": 1, "pages_succeeded": 1,
        "fetched_items": 1, "parsed_items": 1, "unique_ids": 1,
        "normalized_items": 1, "rejected_items": 0,
        "inserted": 0, "updated": 0, "unchanged": 1,
        "withdrawn": 0, "reappeared": 0, "expected_count": None,
        "dataset_id": None, "retries": 0, "truncation_signals": [], "error": None,
        "seen_ids": ["ofim-1"],
        "snapshot_proof": "all_target_routes_exhausted",
    }
    partial = {**complete, "source": "leboncoin", "status": "partial",
               "truncation_signals": ["page_cap:1"]}
    failed = {**complete, "source": "citya", "status": "failed", "pages_succeeded": 0,
              "fetched_items": 0, "parsed_items": 0, "unique_ids": 0,
              "normalized_items": 0, "unchanged": 0, "error": "timeout", "seen_ids": []}
    assert realestate_watch.authoritative_withdrawal_sources([complete, partial, failed]) == ["ofim"]


def test_live_gate_blocks_leboncoin_partial_below_floor_even_if_source_ratio_is_green():
    results = [
        _runner("ofim", {"by_source": {"ofim": 20}}),
        _runner("leboncoin", {"by_source": {"leboncoin": 35}}),
    ]
    manifests = [
        {
            "run_id": "run-1", "source": "ofim", "status": "complete", "attempted": True,
            "pages_attempted": 2, "pages_succeeded": 2, "fetched_items": 20, "parsed_items": 20,
            "unique_ids": 20, "normalized_items": 20, "rejected_items": 0, "inserted": 0,
            "updated": 0, "unchanged": 20, "withdrawn": 0, "reappeared": 0,
            "expected_count": 20, "dataset_id": None, "retries": 0,
            "truncation_signals": [], "error": None,
            "seen_ids": [f"ofim-{index}" for index in range(20)],
        },
        {
            "run_id": "run-1", "source": "leboncoin", "status": "partial", "attempted": True,
            "pages_attempted": 4, "pages_succeeded": 4, "fetched_items": 35, "parsed_items": 35,
            "unique_ids": 35, "normalized_items": 35, "rejected_items": 0, "inserted": 0,
            "updated": 0, "unchanged": 35, "withdrawn": 0, "reappeared": 0,
            "expected_count": 268, "dataset_id": "ds", "retries": 0,
            "truncation_signals": ["page_cap:1"], "error": None,
            "seen_ids": [f"lbc-{index}" for index in range(35)],
        },
    ]
    gate = realestate_watch.evaluate_source_gate(results, source_manifests=manifests)
    assert gate["ok_ratio"] == 1.0
    assert gate["ok"] is False
    assert gate["manifest_gate"]["blocking_sources"] == ["leboncoin"]
