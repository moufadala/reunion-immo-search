from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from src.pipeline_reconciliation_runtime import (
    _preferred_normalized,
    build_runtime_reconciliation,
    discover_before_db,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_pipeline_reconciliation.py"


def test_policy_input_uses_raw_value_when_enrichment_is_a_placeholder():
    assert _preferred_normalized("Autre", "Saint-Denis") == "Saint-Denis"
    assert _preferred_normalized("zone-non-precisee", "Le Chaudron") == "Le Chaudron"


def _write_db(
    path: Path,
    rows: list[tuple[str, str, int, float, int]],
) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.execute(
            "CREATE TABLE rental_listings ("
            "source_site TEXT NOT NULL, source_id TEXT NOT NULL, "
            "is_active INTEGER, surface_m2 REAL, rent_eur INTEGER, "
            "city TEXT, district TEXT, property_type TEXT, title TEXT, "
            "description TEXT, PRIMARY KEY(source_site, source_id))"
        )
        con.executemany(
            "INSERT INTO rental_listings VALUES (?,?,?,?,?,"
            "'Saint-Denis','Le Chaudron','apartment','T3','Description complète')",
            rows,
        )
        con.commit()


def _manifest_bundle() -> dict:
    return {
        "run_id": "run-1",
        "sources": [
            {
                "run_id": "run-1",
                "source": "ofim",
                "status": "complete",
                "attempted": True,
                "pages_attempted": 1,
                "pages_succeeded": 1,
                "fetched_items": 4,
                "parsed_items": 4,
                "unique_ids": 4,
                "normalized_items": 4,
                "rejected_items": 0,
                "inserted": 1,
                "updated": 1,
                "unchanged": 2,
                "withdrawn": 1,
                "reappeared": 1,
                "expected_count": 4,
                "dataset_id": None,
                "retries": 0,
                "truncation_signals": [],
                "error": None,
                "seen_ids": ["a", "new", "gone", "back"],
            }
        ],
    }


def _feed() -> dict:
    return {
        "meta": {
            "reconciliation_product": {
                "active_input": 3,
                "policy_exclusions": {"surface_below_65": 1},
                "eligible": 2,
                "dedup_hidden": 1,
                "visible": 1,
                "eligible_ids": ["ofim:a", "ofim:new"],
                "visible_ids": ["ofim:a"],
                "also_on_ids": ["ofim:new"],
                "excluded_ids": {"ofim:back": "surface_below_65"},
                "fields": {
                    "missing_title": 0,
                    "missing_rent": 0,
                    "missing_surface": 0,
                    "missing_commune": 0,
                    "missing_description": 0,
                    "missing_photo": 0,
                },
                "field_explanations": {},
                "field_explanation_reasons": {},
            }
        },
        "listings": [{
            "id": "ofim:a",
            "description": "Texte source disponible.",
            "image": "/thumbs/a.webp",
            "images": ["/thumbs/a.webp"],
            "also_on": [
                {"id": "ofim:a", "source": "ofim"},
                {"id": "ofim:new", "source": "zimo"},
            ],
        }],
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    before = tmp_path / "before.db"
    current = tmp_path / "current.db"
    feed = tmp_path / "feed.json"
    manifests = tmp_path / "source_run_manifests.json"
    _write_db(
        before,
        [
            ("ofim", "a", 1, 70, 1200),
            ("ofim", "gone", 1, 70, 1200),
            ("ofim", "back", 0, 60, 1200),
        ],
    )
    _write_db(
        current,
        [
            ("ofim", "a", 1, 70, 1200),
            ("ofim", "gone", 0, 70, 1200),
            ("ofim", "back", 1, 60, 1200),
            ("ofim", "new", 1, 70, 1200),
        ],
    )
    feed.write_text(json.dumps(_feed()), encoding="utf-8")
    manifests.write_text(json.dumps(_manifest_bundle()), encoding="utf-8")
    return before, current, feed, manifests


def test_runtime_builder_reconciles_sqlite_transitions_and_real_feed_ids(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["report"]["database"] == {
        "total": 4,
        "active": 3,
        "inactive": 1,
        "new": 1,
        "disappeared": 1,
        "reappeared": 1,
    }
    assert result["evidence"]["feed_visible_ids"] == 1


def test_runtime_builder_rejects_visible_content_even_when_counters_claim_zero(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)
    payload = json.loads(feed.read_text(encoding="utf-8"))
    payload["listings"][0].update(
        description=" ",
        image=" ",
        images=[None, ""],
    )
    feed.write_text(json.dumps(payload), encoding="utf-8")

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )

    assert result["ok"] is False
    assert any(
        "feed visible listings missing description: 1" in error
        for error in result["errors"]
    )
    assert any("feed visible listings missing photo: 1" in error for error in result["errors"])


def test_runtime_builder_blocks_identity_claim_not_present_in_feed(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)
    payload = json.loads(feed.read_text(encoding="utf-8"))
    payload["listings"] = [{"id": "ofim:wrong"}]
    feed.write_text(json.dumps(payload), encoding="utf-8")

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )

    assert result["ok"] is False
    assert any("feed visible identities differ" in error for error in result["errors"])


def test_runtime_builder_blocks_product_active_counter_different_from_sqlite(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)
    payload = json.loads(feed.read_text(encoding="utf-8"))
    product = payload["meta"]["reconciliation_product"]
    product.update(
        active_input=2,
        policy_exclusions={"surface_below_65": 1},
        eligible=1,
        dedup_hidden=0,
        visible=1,
        eligible_ids=["ofim:a"],
        visible_ids=["ofim:a"],
        also_on_ids=[],
    )
    feed.write_text(json.dumps(payload), encoding="utf-8")

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )

    assert result["ok"] is False
    assert any("product.active_input=2 differs from database.active=3" in error for error in result["errors"])


def test_runtime_builder_blocks_hidden_identity_without_actual_also_on_link(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)
    payload = json.loads(feed.read_text(encoding="utf-8"))
    payload["listings"][0].pop("also_on")
    feed.write_text(json.dumps(payload), encoding="utf-8")

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )

    assert result["ok"] is False
    assert any(
        "eligible identities missing without an actual also_on link" in error
        for error in result["errors"]
    )


def test_runtime_builder_blocks_active_identity_traced_only_by_exclusion_count(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)
    with sqlite3.connect(current) as con:
        con.execute("UPDATE rental_listings SET is_active=0 WHERE source_id='new'")
    payload = json.loads(feed.read_text(encoding="utf-8"))
    product = payload["meta"]["reconciliation_product"]
    product.update(
        active_input=2,
        eligible=1,
        dedup_hidden=0,
        visible=1,
        eligible_ids=["ofim:a"],
        visible_ids=["ofim:a"],
        also_on_ids=[],
    )
    product.pop("excluded_ids")
    payload["listings"][0]["also_on"] = [{"id": "ofim:a", "source": "ofim"}]
    feed.write_text(json.dumps(payload), encoding="utf-8")

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )

    assert result["ok"] is False
    assert any(
        "active database identities missing from product accounting: ofim:back" in error
        for error in result["errors"]
    )


def test_runtime_builder_blocks_overlap_between_eligible_and_excluded_ids(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)
    payload = json.loads(feed.read_text(encoding="utf-8"))
    product = payload["meta"]["reconciliation_product"]
    product["excluded_ids"]["ofim:a"] = "surface_below_65"
    product["policy_exclusions"]["surface_below_65"] = 2
    feed.write_text(json.dumps(payload), encoding="utf-8")

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )

    assert result["ok"] is False
    assert any(
        "product eligible_ids and excluded_ids overlap: ofim:a" in error
        for error in result["errors"]
    )


def test_runtime_builder_re_evaluates_each_exclusion_reason_from_sqlite(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)
    payload = json.loads(feed.read_text(encoding="utf-8"))
    product = payload["meta"]["reconciliation_product"]
    product["excluded_ids"]["ofim:back"] = "rent_above_1700"
    product["policy_exclusions"] = {"rent_above_1700": 1}
    feed.write_text(json.dumps(payload), encoding="utf-8")

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )

    assert result["ok"] is False
    assert any(
        "excluded identity ofim:back reason mismatch: "
        "claimed=rent_above_1700 evaluated=surface_below_65" in error
        for error in result["errors"]
    )


def test_runtime_builder_blocks_manifest_transition_counter_different_from_sqlite(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)
    payload = json.loads(manifests.read_text(encoding="utf-8"))
    payload["sources"][0].update(inserted=0, updated=2)
    manifests.write_text(json.dumps(payload), encoding="utf-8")

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )

    assert result["ok"] is False
    assert any("database: new must equal sum(source.inserted)" in error for error in result["errors"])


def test_runtime_builder_blocks_a_database_identity_deleted_between_snapshots(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)
    with sqlite3.connect(current) as con:
        con.execute("DELETE FROM rental_listings WHERE source_id='gone'")

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim"},
    )

    assert result["ok"] is False
    assert any("database identities deleted" in error for error in result["errors"])


def test_runtime_builder_blocks_a_missing_expected_source(tmp_path):
    before, current, feed, manifests = _inputs(tmp_path)

    result = build_runtime_reconciliation(
        feed_path=feed,
        manifests_path=manifests,
        db_path=current,
        before_db_path=before,
        expected_sources={"ofim", "seloger"},
    )

    assert result["ok"] is False
    assert any("missing source manifests: seloger" in error for error in result["errors"])


def test_cli_discovers_the_unique_watcher_backup_and_writes_gate(tmp_path):
    run = tmp_path / "run-1"
    watcher = run / "realestate_watch"
    backups = watcher / "backups"
    backups.mkdir(parents=True)
    before, current, feed, manifests = _inputs(tmp_path)
    discovered = backups / "reunion_watch.stage.db.bak.20260818T000000Z"
    before.replace(discovered)
    manifest_target = run / "source_run_manifests.json"
    manifests.replace(manifest_target)
    output = run / "pipeline_reconciliation.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--feed",
            str(feed),
            "--source-manifests",
            str(manifest_target),
            "--db",
            str(current),
            "--expected-source",
            "ofim",
            "--out",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["evidence"]["before_db"] == str(discovered)


def test_backup_discovery_fails_closed_when_multiple_snapshots_are_ambiguous(tmp_path):
    run = tmp_path / "run-1"
    backups = run / "realestate_watch" / "backups"
    backups.mkdir(parents=True)
    manifests = run / "source_run_manifests.json"
    manifests.write_text(json.dumps(_manifest_bundle()), encoding="utf-8")
    (backups / "one.db.bak.1").write_bytes(b"one")
    (backups / "two.db.bak.2").write_bytes(b"two")

    try:
        discover_before_db(manifests)
    except ValueError as exc:
        assert "found 2" in str(exc)
    else:
        raise AssertionError("ambiguous DB backups must fail closed")
