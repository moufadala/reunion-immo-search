from __future__ import annotations

from scripts.realestate_multi_sources_scraper import build_source_manifest


class Listing:
    source_id = "kept-1"


def test_accounted_unparsed_items_still_prevent_a_complete_snapshot():
    manifest = build_source_manifest(
        source="leboncoin",
        run_id="run-parse-loss",
        listings=[Listing()],
        event_statuses=["seen"],
        source_status={"ok": True, "count": 1},
        fetch_log=[{"ok": True}],
        runtime_meta={
            "pages_attempted": 1,
            "pages_succeeded": 1,
            "raw_items": 10,
            "parsed_items": 1,
            "unique_ids": 1,
            "unparsed_items_by_reason": {"malformed_item": 9},
            "pre_unique_rejections_by_reason": {},
            "rejected_items_by_reason": {},
            "full_snapshot_proof": True,
            "snapshot_proof": "actor_finished",
        },
    )

    assert manifest["status"] == "partial"
    assert "unparsed_items_present:9" in manifest["truncation_signals"]
