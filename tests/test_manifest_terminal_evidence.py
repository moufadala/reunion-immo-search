import pytest

from src.pipeline_reconciliation import ManifestValidationError, SourceRunManifest


def test_complete_source_requires_terminal_or_dataset_evidence():
    payload = {
        "run_id": "run-1", "source": "ofim", "status": "complete",
        "attempted": True, "pages_attempted": 1, "pages_succeeded": 1,
        "fetched_items": 0, "parsed_items": 0, "unique_ids": 0,
        "normalized_items": 0, "rejected_items": 0,
        "inserted": 0, "updated": 0, "unchanged": 0,
        "withdrawn": 0, "reappeared": 0, "expected_count": None,
        "dataset_id": None, "retries": 0, "truncation_signals": [],
        "error": None, "seen_ids": [],
    }

    with pytest.raises(ManifestValidationError, match="complete source requires terminal evidence"):
        SourceRunManifest.from_dict(payload)

    payload["snapshot_proof"] = "all_target_routes_exhausted"
    assert SourceRunManifest.from_dict(payload).authoritative_for_withdrawals is True
