from __future__ import annotations

import pytest

from src.pipeline_reconciliation import ManifestValidationError, SourceRunManifest


def _manifest(**overrides):
    payload = {
        "run_id": "run-1",
        "source": "ofim",
        "status": "complete",
        "attempted": True,
        "pages_attempted": 1,
        "pages_succeeded": 1,
        "fetched_items": 3,
        "parsed_items": 3,
        "unique_ids": 3,
        "normalized_items": 2,
        "rejected_items": 1,
        "inserted": 0,
        "updated": 0,
        "unchanged": 2,
        "withdrawn": 0,
        "reappeared": 0,
        "expected_count": 3,
        "dataset_id": None,
        "retries": 0,
        "truncation_signals": [],
        "error": None,
        "rejected_items_by_reason": {"outside_scope": 1},
    }
    payload.update(overrides)
    payload.setdefault(
        "seen_ids", [f"id-{index}" for index in range(payload["normalized_items"])]
    )
    return payload


def test_manifest_accepts_rejections_only_when_every_item_has_a_reason():
    item = SourceRunManifest.from_dict(_manifest())

    assert item.rejected_items_by_reason == (("outside_scope", 1),)
    assert item.to_dict()["rejected_items_by_reason"] == {"outside_scope": 1}


@pytest.mark.parametrize(
    "reasons",
    [None, {}, {"outside_scope": 0}, {"outside_scope": 2}, {"": 1}],
)
def test_manifest_rejects_missing_or_inexact_rejection_accounting(reasons):
    payload = _manifest()
    if reasons is None:
        payload.pop("rejected_items_by_reason")
    else:
        payload["rejected_items_by_reason"] = reasons

    with pytest.raises(ManifestValidationError, match="rejected_items_by_reason"):
        SourceRunManifest.from_dict(payload)


def test_zero_rejections_remains_compatible_with_older_zero_loss_manifests():
    item = SourceRunManifest.from_dict(
        _manifest(
            unique_ids=2,
            fetched_items=2,
            parsed_items=2,
            normalized_items=2,
            rejected_items=0,
            expected_count=2,
            rejected_items_by_reason={},
        )
    )

    assert item.rejected_items_by_reason == ()
