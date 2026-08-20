from __future__ import annotations

import pytest

from src.pipeline_reconciliation import ManifestValidationError, SourceRunManifest


def _payload(**overrides):
    payload = {
        "run_id": "run-1",
        "source": "ofim",
        "status": "complete",
        "attempted": True,
        "pages_attempted": 1,
        "pages_succeeded": 1,
        "fetched_items": 2,
        "parsed_items": 2,
        "unique_ids": 2,
        "normalized_items": 2,
        "rejected_items": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 2,
        "withdrawn": 0,
        "reappeared": 0,
        "expected_count": 2,
        "dataset_id": None,
        "retries": 0,
        "truncation_signals": [],
        "error": None,
        "rejected_items_by_reason": {},
        "pre_unique_rejections_by_reason": {},
        "unparsed_items_by_reason": {},
        "seen_ids": ["a", "b"],
    }
    payload.update(overrides)
    return payload


def test_complete_source_must_have_been_attempted():
    with pytest.raises(ManifestValidationError, match="complete source must be attempted"):
        SourceRunManifest.from_dict(
            _payload(
                attempted=False,
                pages_attempted=0,
                pages_succeeded=0,
                fetched_items=0,
                parsed_items=0,
                unique_ids=0,
                normalized_items=0,
                unchanged=0,
                expected_count=None,
                seen_ids=[],
            )
        )


def test_unparsed_gap_requires_exact_reasons():
    with pytest.raises(ManifestValidationError, match="unparsed_items_by_reason"):
        SourceRunManifest.from_dict(
            _payload(fetched_items=5, parsed_items=2)
        )


def test_pre_unique_gap_requires_exact_reasons():
    with pytest.raises(ManifestValidationError, match="pre_unique_rejections_by_reason"):
        SourceRunManifest.from_dict(
            _payload(fetched_items=3, parsed_items=3, unique_ids=2)
        )


def test_seen_ids_are_required_distinct_and_match_normalized_items():
    with pytest.raises(ManifestValidationError, match="missing manifest counters: seen_ids"):
        payload = _payload()
        payload.pop("seen_ids")
        SourceRunManifest.from_dict(payload)

    with pytest.raises(ManifestValidationError, match="seen_ids must be distinct"):
        SourceRunManifest.from_dict(_payload(seen_ids=["a", "a"]))

    with pytest.raises(ManifestValidationError, match="seen_ids must match normalized_items"):
        SourceRunManifest.from_dict(_payload(seen_ids=["a"]))


def test_every_transition_is_accounted_with_valid_evidence():
    manifest = SourceRunManifest.from_dict(
        _payload(
            fetched_items=5,
            parsed_items=4,
            unique_ids=3,
            normalized_items=2,
            rejected_items=1,
            rejected_items_by_reason={"commercial": 1},
            pre_unique_rejections_by_reason={"duplicate_raw": 1},
            unparsed_items_by_reason={"mapping_error": 1},
            expected_count=3,
        )
    )

    assert manifest.seen_ids == ("a", "b")
    assert manifest.to_dict()["pre_unique_rejections_by_reason"] == {"duplicate_raw": 1}
