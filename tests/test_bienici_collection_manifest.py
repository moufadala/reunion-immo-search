from __future__ import annotations

import importlib.util
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from src.pipeline_reconciliation import SourceRunManifest



SCRIPT = Path(__file__).parents[1] / "scripts" / "bienici_rental_scraper.py"
SPEC = importlib.util.spec_from_file_location("bienici_rental_scraper", SCRIPT)
assert SPEC and SPEC.loader
bienici = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bienici
SPEC.loader.exec_module(bienici)


def _ad(identifier: str, postal: str = "97400", kind: str = "flat") -> dict:
    return {
        "id": identifier,
        "postalCode": postal,
        "propertyType": kind,
        "title": f"Appartement {identifier}",
        "price": 900,
        "surfaceArea": 70,
    }


def test_scope_contains_only_saint_denis_and_sainte_marie_postal_codes():
    assert bienici.TARGET_POSTAL_CODES == {"97400", "97490", "97417", "97495", "97438"}
    assert bienici.classify_ad(_ad("outside-east", "97441")) == "postal_scope"
    assert bienici.classify_ad(_ad("outside-east-2", "97440")) == "postal_scope"
    assert bienici.classify_ad(_ad("commercial", "97400", "commercial")) == "commercial_or_nonresidential"
    assert bienici.classify_ad(_ad("target", "97438")) is None


def test_reported_total_proves_exhaustion_without_fetching_an_extra_page():
    payloads = [
        {"total": 3, "realEstateAds": [_ad("a"), _ad("b")]},
        {"total": 3, "realEstateAds": [_ad("c", "97438")]},
    ]
    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        return payloads.pop(0)

    result = bienici.collect_pages(fetch, size=2, max_pages=5)
    assert result.complete is True
    assert result.pages_attempted == 2
    assert result.pages_succeeded == 2
    assert result.reported_total == 3
    assert [ad["id"] for ad in result.ads] == ["a", "b", "c"]
    assert len(calls) == 2


def test_page_cap_before_reported_total_is_partial_and_named():
    def fetch(_url: str) -> dict:
        return {"total": 9, "realEstateAds": [_ad("a"), _ad("b")]}

    result = bienici.collect_pages(fetch, size=2, max_pages=2)
    manifest = bienici.build_manifest(
        result, run_id="run-1", event_statuses=["seen"], normalized_ids={"a"},
        rejection_counts={"duplicate_id": 1, "postal_scope": 1, "commercial_or_nonresidential": 1},
    )

    assert result.complete is False
    assert result.truncation_signals == ["page_cap:2"]
    assert manifest["status"] == "partial"
    assert manifest["withdrawn"] == 0
    assert manifest["expected_count"] == 9
    assert manifest["rejected_items_by_reason"] == {
        "commercial_or_nonresidential": 1,
        "postal_scope": 1,
    }
    assert manifest["pre_unique_rejections_by_reason"] == {"duplicate_id": 1}


def test_successful_empty_page_is_exhaustion_proof_when_api_has_no_total():
    pages = [
        {"realEstateAds": [_ad("a")]},
        {"realEstateAds": []},
    ]
    result = bienici.collect_pages(lambda _url: pages.pop(0), size=1, max_pages=4)
    assert result.complete is True
    assert result.reported_total == 1
    assert result.truncation_signals == []


def test_first_page_failure_emits_failed_manifest_not_complete():
    def fail(_url: str) -> dict:
        raise TimeoutError("timeout")

    result = bienici.collect_pages(fail, size=50, max_pages=3)
    manifest = bienici.build_manifest(
        result, run_id="run-1", event_statuses=[], normalized_ids=set(), rejection_counts={}
    )
    assert result.complete is False
    assert manifest["status"] == "failed"
    assert manifest["pages_attempted"] == 1
    assert manifest["pages_succeeded"] == 0
    assert "timeout" in manifest["error"]


def test_later_page_failure_is_partial_and_never_authoritative():
    pages = [{"total": 4, "realEstateAds": [_ad("a"), _ad("b")]}]

    def fetch(_url: str) -> dict:
        if pages:
            return pages.pop(0)
        raise TimeoutError("page two timed out")

    result = bienici.collect_pages(fetch, size=2, max_pages=3)
    manifest = bienici.build_manifest(
        result,
        run_id="run-later-error",
        event_statuses=["seen", "seen"],
        normalized_ids={"a", "b"},
        rejection_counts={},
    )

    assert manifest["status"] == "partial"
    assert manifest["withdrawn"] == 0
    assert manifest["terminal_reason"] == "page_error"
    assert "page_error:2" in manifest["truncation_signals"]
    assert "page two timed out" in manifest["error"]


def test_reported_total_high_watermark_prevents_false_complete_when_total_shrinks():
    payloads = [
        {"total": 5, "realEstateAds": [_ad("a"), _ad("b")]},
        {"total": 2, "realEstateAds": [_ad("c"), _ad("d")]},
    ]
    result = bienici.collect_pages(lambda _url: payloads.pop(0), size=2, max_pages=2)

    assert result.complete is False
    assert result.reported_total == 5
    assert result.terminal_reason == "page_cap"
    assert result.truncation_signals == ["page_cap:2"]


def test_collection_artifact_hash_covers_exact_written_bytes(tmp_path: Path):
    payloads = [{"total": 1, "realEstateAds": [_ad("a")]}]
    result = bienici.collect_pages(lambda _url: payloads.pop(0), size=50, max_pages=3)
    artifact = tmp_path / "bienici-collection.json"

    artifact_sha = bienici.write_collection_artifact(result, artifact)
    manifest = bienici.build_manifest(
        result,
        run_id="run-hash",
        event_statuses=["seen"],
        normalized_ids={"a"},
        rejection_counts={},
        artifact_path=artifact,
    )

    assert artifact_sha == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert manifest["artifact_path"] == str(artifact)
    assert manifest["artifact_sha256"] == artifact_sha
    assert json.loads(artifact.read_text(encoding="utf-8"))["terminal_reason"] == "reported_total_reached"


def test_complete_manifest_is_accepted_by_strict_shared_contract(tmp_path: Path):
    from src.pipeline_reconciliation import SourceRunManifest

    payloads = [{"total": 1, "realEstateAds": [_ad("a")]}]
    result = bienici.collect_pages(lambda _url: payloads.pop(0), size=50, max_pages=3)
    artifact = tmp_path / "bienici-collection.json"
    bienici.write_collection_artifact(result, artifact)
    manifest = bienici.build_manifest(
        result,
        run_id="run-contract",
        event_statuses=["seen"],
        normalized_ids={"a"},
        rejection_counts={},
        artifact_path=artifact,
    )

    parsed = SourceRunManifest.from_dict(manifest)
    assert parsed.status == "complete"
    assert parsed.authoritative_for_withdrawals is True


def test_proven_zero_total_is_complete_and_authoritative(tmp_path: Path):
    result = bienici.collect_pages(
        lambda _url: {"total": 0, "realEstateAds": []},
        size=50,
        max_pages=3,
    )
    artifact = tmp_path / "bienici-zero.json"
    bienici.write_collection_artifact(result, artifact)
    manifest = bienici.build_manifest(
        result,
        run_id="run-zero",
        event_statuses=[],
        normalized_ids=set(),
        rejection_counts={},
        artifact_path=artifact,
    )

    assert result.complete is True
    assert result.reported_total == 0
    assert manifest["status"] == "complete"
    assert SourceRunManifest.from_dict(manifest).authoritative_for_withdrawals


def test_zero_without_any_terminal_proof_is_failed_not_complete(tmp_path: Path):
    result = bienici.CollectionResult()
    artifact = tmp_path / "bienici-unattempted.json"
    bienici.write_collection_artifact(result, artifact)
    manifest = bienici.build_manifest(
        result, run_id="run-unattempted", event_statuses=[],
        normalized_ids=set(), rejection_counts={}, artifact_path=artifact,
    )

    assert manifest["status"] == "failed"
    assert manifest["status"] != "complete"
    parsed = SourceRunManifest.from_dict(manifest)
    assert parsed.status == "failed"
    assert parsed.error


def test_upsert_reports_reappeared_when_an_inactive_listing_is_seen_again(tmp_path: Path):
    connection = sqlite3.connect(":memory:")
    bienici.init_db(connection)
    listing = bienici.normalize(_ad("returns"), raw_dir=tmp_path / "raw")

    assert bienici.upsert(connection, listing) == "new"
    connection.execute(
        "UPDATE rental_listings SET is_active=0 WHERE source_site=? AND source_id=?",
        (listing.source_site, listing.source_id),
    )

    assert bienici.upsert(connection, listing) == "reappeared"
    assert connection.execute(
        "SELECT is_active FROM rental_listings WHERE source_site=? AND source_id=?",
        (listing.source_site, listing.source_id),
    ).fetchone()[0] == 1
    connection.close()


def test_main_uses_pipeline_run_id_from_environment(monkeypatch, tmp_path: Path):
    result = bienici.CollectionResult(
        ads=[], page_payloads=[{"total": 0, "realEstateAds": []}],
        urls=["https://example.test"], page_sizes=[0], reported_totals=[0],
        reported_total=0, pages_attempted=1, pages_succeeded=1,
        complete=True, terminal_reason="reported_total_reached",
    )
    monkeypatch.setenv("IMMO_RUN_ID", "pipeline-20260820")
    monkeypatch.setattr(bienici, "collect_pages", lambda *_args, **_kwargs: result)
    artifact_dir = tmp_path / "artifacts"
    manifest_path = tmp_path / "manifest.json"

    rc = bienici.main([
        "--dry-run", "--artifact-dir", str(artifact_dir),
        "--manifest", str(manifest_path),
    ])

    assert rc == 0
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "pipeline-20260820"
    assert (artifact_dir / "collection-pipeline-20260820.json").is_file()
