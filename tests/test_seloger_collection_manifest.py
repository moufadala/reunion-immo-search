from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.pipeline_reconciliation import SourceRunManifest
from src.seloger_collection_manifest import (
    build_seloger_manifest,
    evaluate_seloger_collection,
    extract_reported_total,
)


def _artifact(tmp_path: Path, count: int) -> Path:
    path = tmp_path / "seloger.json"
    path.write_text(
        json.dumps({"annonces": [{"id": str(i)} for i in range(count)]}),
        encoding="utf-8",
    )
    return path


def test_extract_reported_total_from_current_french_heading_shapes():
    assert extract_reported_total("256 annonces à louer à La Réunion") == 256
    assert extract_reported_total("1 234 biens disponibles") == 1234
    assert extract_reported_total("Résultats de location") is None


def test_reported_total_reached_is_complete_with_page_sizes_and_hash(tmp_path: Path):
    artifact = _artifact(tmp_path, 76)
    evidence = evaluate_seloger_collection(
        page_sizes=[30, 30, 16],
        unique_ids=76,
        reported_total=76,
        terminal_reason="short_page",
    )
    manifest = build_seloger_manifest(
        evidence,
        run_id="seloger-1",
        artifact_path=artifact,
        normalized_ids={str(i) for i in range(76)},
        event_statuses=["seen"] * 76,
    )

    assert evidence.complete is True
    assert evidence.terminal_reason == "reported_total_reached"
    assert manifest["status"] == "complete"
    assert manifest["page_sizes"] == [30, 30, 16]
    assert manifest["last_page_short"] is True
    assert manifest["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert SourceRunManifest.from_dict(manifest).authoritative_for_withdrawals is True


def test_short_last_page_without_total_is_explicit_exhaustion_proof(tmp_path: Path):
    evidence = evaluate_seloger_collection(
        page_sizes=[30, 7],
        unique_ids=37,
        reported_total=None,
        terminal_reason="short_page",
    )
    manifest = build_seloger_manifest(
        evidence,
        run_id="seloger-short",
        artifact_path=_artifact(tmp_path, 37),
        normalized_ids={str(i) for i in range(37)},
        event_statuses=["seen"] * 37,
    )

    assert evidence.complete is True
    assert manifest["status"] == "complete"
    assert manifest["expected_count"] == 37


def test_full_last_page_at_page_cap_is_partial_not_complete(tmp_path: Path):
    evidence = evaluate_seloger_collection(
        page_sizes=[30] * 9,
        unique_ids=270,
        reported_total=None,
        terminal_reason="page_cap",
    )
    manifest = build_seloger_manifest(
        evidence,
        run_id="seloger-cap",
        artifact_path=_artifact(tmp_path, 270),
        normalized_ids={str(i) for i in range(270)},
        event_statuses=["seen"] * 270,
    )

    assert evidence.complete is False
    assert evidence.truncation_signals == ["page_cap"]
    assert manifest["status"] == "partial"
    assert manifest["withdrawn"] == 0


def test_missing_next_button_before_reported_total_is_partial(tmp_path: Path):
    evidence = evaluate_seloger_collection(
        page_sizes=[30, 30],
        unique_ids=60,
        reported_total=90,
        terminal_reason="no_next_page_button",
    )
    manifest = build_seloger_manifest(
        evidence,
        run_id="seloger-missing-button",
        artifact_path=_artifact(tmp_path, 60),
        normalized_ids={str(i) for i in range(60)},
        event_statuses=["seen"] * 60,
    )

    assert evidence.complete is False
    assert "reported_total_gap:60/90" in evidence.truncation_signals
    assert manifest["status"] == "partial"


def test_repeated_page_is_partial_even_when_every_dom_read_succeeded(tmp_path: Path):
    evidence = evaluate_seloger_collection(
        page_sizes=[30, 30],
        unique_ids=30,
        reported_total=None,
        terminal_reason="repeated_page",
    )
    manifest = build_seloger_manifest(
        evidence,
        run_id="seloger-repeat",
        artifact_path=_artifact(tmp_path, 30),
        normalized_ids={str(i) for i in range(30)},
        event_statuses=["seen"] * 30,
    )

    assert evidence.complete is False
    assert "repeated_page" in manifest["truncation_signals"]
    assert manifest["status"] == "partial"


def test_first_page_error_is_failed_and_later_error_is_partial(tmp_path: Path):
    first = evaluate_seloger_collection(
        page_sizes=[],
        unique_ids=0,
        reported_total=None,
        terminal_reason="page_error",
        pages_attempted=1,
        pages_succeeded=0,
        error="CDP unavailable",
    )
    later = evaluate_seloger_collection(
        page_sizes=[30],
        unique_ids=30,
        reported_total=60,
        terminal_reason="page_error",
        pages_attempted=2,
        pages_succeeded=1,
        error="second page timeout",
    )

    first_manifest = build_seloger_manifest(
        first,
        run_id="seloger-failed",
        artifact_path=_artifact(tmp_path, 0),
        normalized_ids=set(),
        event_statuses=[],
    )
    later_manifest = build_seloger_manifest(
        later,
        run_id="seloger-partial",
        artifact_path=_artifact(tmp_path, 30),
        normalized_ids={str(i) for i in range(30)},
        event_statuses=["seen"] * 30,
    )

    assert first_manifest["status"] == "failed"
    assert later_manifest["status"] == "partial"
    assert later_manifest["withdrawn"] == 0


def test_live_collector_wires_manifest_and_fails_closed_on_partial():
    collector = (
        Path(__file__).parents[1] / "scripts" / "seloger_multi_page.py"
    ).read_text(encoding="utf-8")

    assert "evaluate_seloger_collection(" in collector
    assert "build_seloger_manifest(" in collector
    assert "seloger_collection_manifest.provisional.json" in collector
    assert '"source_manifest": str(manifest_path)' in collector
    assert 'return 0 if manifest["status"] == "complete" else 2' in collector


def test_live_collector_does_not_use_a_nine_page_business_cap():
    collector = (
        Path(__file__).parents[1] / "scripts" / "seloger_multi_page.py"
    ).read_text(encoding="utf-8")

    assert "MAX_PAGES = 9" not in collector
    assert "SELOGER_MAX_PAGES" in collector
    assert "IMMO_REFRESH_RUN_DIR" in collector


def test_proven_zero_total_is_complete_and_authoritative(tmp_path: Path):
    evidence = evaluate_seloger_collection(
        page_sizes=[0],
        unique_ids=0,
        reported_total=0,
        terminal_reason="empty_page",
    )
    artifact = _artifact(tmp_path, 0)
    manifest = build_seloger_manifest(
        evidence,
        run_id="seloger-zero",
        artifact_path=artifact,
        normalized_ids=set(),
        event_statuses=[],
    )

    assert evidence.complete is True
    assert manifest["status"] == "complete"
    assert SourceRunManifest.from_dict(manifest).authoritative_for_withdrawals


def test_zero_without_terminal_proof_is_partial_not_complete(tmp_path: Path):
    evidence = evaluate_seloger_collection(
        page_sizes=[],
        unique_ids=0,
        reported_total=None,
        terminal_reason="unknown_terminal",
        pages_attempted=0,
        pages_succeeded=0,
    )
    manifest = build_seloger_manifest(
        evidence,
        run_id="seloger-zero-unproven",
        artifact_path=_artifact(tmp_path, 0),
        normalized_ids=set(),
        event_statuses=[],
    )

    assert evidence.complete is False
    assert manifest["status"] == "partial"
