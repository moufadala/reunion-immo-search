from __future__ import annotations

from src.source_manifest_bundle import (
    CRITICAL_PORTALS,
    merge_and_gate_source_manifests,
)


def _complete(source: str, run_id: str = "run-1") -> dict:
    return {
        "run_id": run_id,
        "source": source,
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
        "seen_ids": [f"{source}:1"],
    }


def test_critical_portal_inventory_is_exactly_fourteen_and_excludes_ofim_rss():
    assert len(CRITICAL_PORTALS) == 14
    assert "seloger" in CRITICAL_PORTALS
    assert "leboncoin" in CRITICAL_PORTALS
    assert "ofim_rss" not in CRITICAL_PORTALS


def test_thirteen_watcher_sources_plus_seloger_pass_as_one_gate():
    watcher_sources = CRITICAL_PORTALS - {"seloger"}
    base = {
        "run_id": "run-1",
        "sources": [_complete(source) for source in sorted(watcher_sources)],
    }

    bundle = merge_and_gate_source_manifests(base, [_complete("seloger")])

    assert bundle["gate"]["ok"] is True
    assert bundle["gate"]["missing_sources"] == []
    assert len(bundle["sources"]) == 14


def test_missing_seloger_blocks_even_when_thirteen_sources_are_complete():
    watcher_sources = CRITICAL_PORTALS - {"seloger"}
    base = {
        "run_id": "run-1",
        "sources": [_complete(source) for source in sorted(watcher_sources)],
    }

    bundle = merge_and_gate_source_manifests(base, [])

    assert bundle["gate"]["ok"] is False
    assert bundle["gate"]["missing_sources"] == ["seloger"]
    assert bundle["gate"]["blocking_sources"] == ["seloger"]


def test_partial_seloger_blocks_and_is_never_hidden_by_complete_watcher_sources():
    watcher_sources = CRITICAL_PORTALS - {"seloger"}
    base = {
        "run_id": "run-1",
        "sources": [_complete(source) for source in sorted(watcher_sources)],
    }
    seloger = {
        **_complete("seloger"),
        "status": "partial",
        "truncation_signals": ["page_cap"],
    }

    bundle = merge_and_gate_source_manifests(base, [seloger])

    assert bundle["gate"]["ok"] is False
    assert bundle["gate"]["blocking_sources"] == ["seloger"]


def test_mismatched_run_id_and_duplicate_source_both_block():
    sources = [_complete(source) for source in sorted(CRITICAL_PORTALS)]
    base = {"run_id": "run-1", "sources": sources}

    bundle = merge_and_gate_source_manifests(
        base,
        [_complete("seloger", run_id="another-run")],
    )

    assert bundle["gate"]["ok"] is False
    assert "seloger" in bundle["gate"]["blocking_sources"]
    assert any("run_id mismatch" in error for error in bundle["gate"]["errors"])
    assert any("duplicate source manifest" in error for error in bundle["gate"]["errors"])

def test_unexpected_fifteenth_source_blocks_the_closed_world_gate():
    base = {
        "run_id": "run-1",
        "sources": [_complete(source) for source in sorted(CRITICAL_PORTALS)],
    }

    bundle = merge_and_gate_source_manifests(base, [_complete("ofim_rss")])

    assert bundle["gate"]["ok"] is False
    assert bundle["gate"]["unexpected_sources"] == ["ofim_rss"]
    assert "ofim_rss" in bundle["gate"]["blocking_sources"]
    assert any("unexpected source manifest" in error for error in bundle["gate"]["errors"])


def test_present_non_blocking_known_sources_are_warnings_not_unexpected(monkeypatch):
    monkeypatch.setenv("IMMO_NON_BLOCKING_REFRESH_SOURCES", "leboncoin,superimmo,seloger")
    complete_sources = CRITICAL_PORTALS - {"leboncoin", "superimmo", "seloger"}
    base = {
        "run_id": "run-1",
        "sources": [_complete(source) for source in sorted(complete_sources)],
    }
    degraded = []
    for source in ("leboncoin", "superimmo", "seloger"):
        degraded.append(
            {
                **_complete(source),
                "status": "partial",
                "truncation_signals": ["degraded_candidate"],
            }
        )

    bundle = merge_and_gate_source_manifests(base, degraded)

    assert bundle["gate"]["ok"] is True
    assert bundle["gate"]["unexpected_sources"] == []
    assert bundle["gate"]["blocking_sources"] == []
    assert bundle["gate"]["non_blocking_sources"] == ["leboncoin", "seloger", "superimmo"]
    assert bundle["gate"]["known_source_count"] == 14
    assert bundle["gate"]["present_known_source_count"] == 14
