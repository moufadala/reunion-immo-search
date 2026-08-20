from __future__ import annotations

import pytest

from src.pipeline_reconciliation import (
    ManifestValidationError,
    SourceRunManifest,
    evaluate_source_manifests,
    reconcile_pipeline,
)


def manifest(**overrides) -> SourceRunManifest:
    payload = {
        "run_id": "run-1",
        "source": "ofim",
        "status": "complete",
        "attempted": True,
        "pages_attempted": 2,
        "pages_succeeded": 2,
        "fetched_items": 3,
        "parsed_items": 3,
        "unique_ids": 3,
        "normalized_items": 3,
        "rejected_items": 0,
        "inserted": 1,
        "updated": 1,
        "unchanged": 1,
        "withdrawn": 0,
        "reappeared": 0,
        "expected_count": 3,
        "dataset_id": None,
        "retries": 0,
        "truncation_signals": (),
        "error": None,
    }
    payload.update(overrides)
    payload.setdefault(
        "seen_ids", [f"id-{index}" for index in range(payload["normalized_items"])]
    )
    return SourceRunManifest.from_dict(payload)


def test_manifest_rejects_an_unexplained_normalization_delta():
    with pytest.raises(ManifestValidationError, match="normalized_items \\+ rejected_items"):
        manifest(fetched_items=4, parsed_items=4, unique_ids=4)


def test_partial_manifest_never_authorizes_withdrawals():
    item = manifest(
        source="leboncoin",
        status="partial",
        expected_count=268,
        fetched_items=35,
        parsed_items=35,
        unique_ids=35,
        normalized_items=35,
        inserted=5,
        updated=10,
        unchanged=20,
        truncation_signals=("page_cap:1",),
    )
    assert item.authoritative_for_withdrawals is False

    with pytest.raises(ManifestValidationError, match="cannot withdraw"):
        manifest(status="partial", withdrawn=1, truncation_signals=("fetch_failure",))


def test_critical_partial_source_below_its_floor_blocks_publication():
    lbc = manifest(
        source="leboncoin",
        status="partial",
        expected_count=268,
        fetched_items=35,
        parsed_items=35,
        unique_ids=35,
        normalized_items=35,
        inserted=0,
        updated=0,
        unchanged=35,
        truncation_signals=("page_cap:1",),
    )
    result = evaluate_source_manifests(
        [lbc], critical_sources={"leboncoin"}, coverage_floors={"leboncoin": 0.9}
    )
    assert result["ok"] is False
    assert result["blocking_sources"] == ["leboncoin"]
    assert result["sources"][0]["coverage_ratio"] == pytest.approx(35 / 268)


def test_critical_partial_source_above_calibrated_floor_still_blocks_publication():
    zimo = manifest(
        source="zimo",
        status="partial",
        expected_count=100,
        fetched_items=60,
        parsed_items=60,
        unique_ids=60,
        normalized_items=60,
        inserted=0,
        updated=0,
        unchanged=60,
        truncation_signals=("bounded_pagination",),
    )
    result = evaluate_source_manifests(
        [zimo], critical_sources={"zimo"}, coverage_floors={"zimo": 0.5}
    )
    assert result["ok"] is False
    assert result["blocking_sources"] == ["zimo"]
    assert result["warning_sources"] == ["zimo"]


def test_attempted_must_be_a_real_boolean_not_a_truthy_string():
    with pytest.raises(ManifestValidationError, match="attempted must be a boolean"):
        manifest(attempted="false")


def test_invalid_manifest_from_generator_keeps_its_source_blocking():
    invalid = manifest().to_dict()
    invalid["normalized_items"] = 2
    result = evaluate_source_manifests(
        (item for item in [invalid]),
        critical_sources={"ofim"},
    )
    assert result["ok"] is False
    assert result["blocking_sources"] == ["ofim"]
    assert result["errors"]


def exact_report() -> dict:
    return {
        "run_id": "run-1",
        "sources": [manifest().to_dict()],
        "database": {"total": 10, "active": 8, "inactive": 2, "new": 1,
                     "disappeared": 0, "reappeared": 0},
        "product": {
            "active_input": 8,
            "policy_exclusions": {"surface_below_65": 1},
            "eligible": 7,
            "dedup_hidden": 1,
            "visible": 6,
            "eligible_ids": ["a", "b", "c", "d", "e", "f", "g"],
            "visible_ids": ["a", "b", "c", "d", "e", "f"],
            "also_on_ids": ["g"],
        },
        "fields": {"missing_title": 0, "missing_rent": 0, "missing_surface": 0,
                   "missing_commune": 0, "missing_description": 0, "missing_photo": 0},
        "field_explanations": {},
    }


def test_reconciliation_accepts_an_exact_identity_and_count_equation():
    result = reconcile_pipeline(exact_report())
    assert result["ok"] is True
    assert result["errors"] == []

def test_visible_content_cannot_be_made_acceptable_by_field_explanations():
    report = exact_report()
    report["fields"].update({"missing_description": 1, "missing_photo": 1})
    report["field_explanations"].update(
        {"missing_description": 1, "missing_photo": 1}
    )

    result = reconcile_pipeline(report)

    assert "fields: visible missing_description=1" in result["errors"]
    assert "fields: visible missing_photo=1" in result["errors"]


def test_reconciliation_fails_on_unknown_product_loss_and_duplicate_identity():
    report = exact_report()
    report["product"].update(
        eligible=6,
        dedup_hidden=0,
        eligible_ids=["a", "a", "b", "c", "d", "e"],
        visible_ids=["a", "b", "c", "d", "e", "f"],
        also_on_ids=[],
    )
    result = reconcile_pipeline(report)
    assert result["ok"] is False
    assert any("active_input - policy_exclusions" in e for e in result["errors"])
    assert any("duplicate eligible identity" in e for e in result["errors"])
    assert any("identity representation mismatch" in e for e in result["errors"])
