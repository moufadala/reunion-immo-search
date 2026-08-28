from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest

from src import import_seloger_multipage as importer
from src.pipeline_reconciliation import SourceRunManifest
from src.seloger_collection_manifest import (
    build_seloger_manifest,
    evaluate_seloger_collection,
)


def _ad(identifier: str, price: int = 900) -> dict:
    return {
        "id": identifier,
        "url": f"https://www.seloger.com/annonces/locations/appartement/saint-denis-974/{identifier}.htm",
        "prix": price,
        "surface": 70,
        "nb_pieces": 3,
        "type_bien": "Appartement",
    }


def _write_artifact(path: Path, ads: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "total": len(ads),
                "with_price": len(ads),
                "annonces": ads,
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_provisional(
    path: Path,
    artifact: Path,
    ads: list[dict],
    run_id: str,
) -> Path:
    evidence = evaluate_seloger_collection(
        page_sizes=[len(ads)],
        unique_ids=len(ads),
        reported_total=len(ads),
        terminal_reason="reported_total_reached",
    )
    manifest = build_seloger_manifest(
        evidence,
        run_id=run_id,
        artifact_path=artifact,
        normalized_ids={str(ad["id"]) for ad in ads},
        event_statuses=["seen"] * len(ads),
    )
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _seed_existing(db: Path, artifact: Path, ads: list[dict]) -> None:
    con = sqlite3.connect(db)
    importer.init_db(con)
    con.execute("""CREATE TABLE IF NOT EXISTS source_absence_state (
        source_site TEXT NOT NULL,
        source_id TEXT NOT NULL,
        successful_missing_runs INTEGER NOT NULL DEFAULT 0,
        last_complete_run_id TEXT,
        PRIMARY KEY(source_site, source_id)
    )""")
    old = "2026-01-01T00:00:00+00:00"
    normalized = {
        item["source_id"]: item
        for item in (importer.normalize_item(ad, artifact) for ad in ads)
    }
    for identifier, active in (("reappear", 0), ("seen", 1)):
        item = normalized[identifier]
        con.execute(
            """INSERT INTO rental_listings
            (source_site,source_id,url,seen_first_at,seen_last_at,content_hash,is_active)
            VALUES ('seloger',?,?,?,?,?,?)""",
            (
                identifier,
                item["url"],
                old,
                old,
                item["content_hash"],
                active,
            ),
        )
    con.execute(
        """INSERT INTO rental_listings
        (source_site,source_id,url,seen_first_at,seen_last_at,content_hash,is_active)
        VALUES ('seloger','gone','https://example.test/gone',?,?,?,1)""",
        (old, old, "old"),
    )
    con.executemany(
        "INSERT INTO source_absence_state VALUES ('seloger',?,1,'older-run')",
        (("reappear",), ("seen",)),
    )
    con.commit()
    con.close()


def test_post_import_manifest_uses_real_new_reappeared_and_unchanged_counts(
    tmp_path: Path,
):
    ads = [_ad("new"), _ad("reappear"), _ad("seen")]
    artifact = _write_artifact(tmp_path / "seloger.json", ads)
    provisional = _write_provisional(
        tmp_path / "provisional.json", artifact, ads, "run-1"
    )
    final = tmp_path / "final.json"
    db = tmp_path / "watch.db"
    _seed_existing(db, artifact, ads)

    result = importer.import_artifact(
        db,
        artifact,
        min_total=1,
        min_prices=1,
        collection_manifest=provisional,
        source_manifest_out=final,
    )

    manifest = json.loads(final.read_text(encoding="utf-8"))
    parsed = SourceRunManifest.from_dict(manifest)
    assert parsed.inserted == 1
    assert parsed.updated == 1
    assert parsed.unchanged == 1
    assert parsed.reappeared == 1
    assert parsed.withdrawn == 0
    assert manifest["event_counts_stage"] == "post_import"
    assert result["db_rows_updated"] == 2
    assert result["reappeared"] == 1

    con = sqlite3.connect(db)
    assert con.execute(
        "SELECT is_active FROM rental_listings WHERE source_id='reappear'"
    ).fetchone()[0] == 1
    assert con.execute(
        "SELECT is_active FROM rental_listings WHERE source_id='gone'"
    ).fetchone()[0] == 1
    assert con.execute(
        """SELECT successful_missing_runs FROM source_absence_state
        WHERE source_site='seloger' AND source_id='gone'"""
    ).fetchone()[0] == 1
    assert con.execute(
        """SELECT COUNT(*) FROM source_absence_state
        WHERE source_site='seloger' AND source_id IN ('reappear','seen')"""
    ).fetchone()[0] == 0
    con.close()


def test_second_distinct_complete_run_records_real_withdrawal_in_final_manifest(
    tmp_path: Path,
):
    ads = [_ad("new"), _ad("reappear"), _ad("seen")]
    artifact = _write_artifact(tmp_path / "seloger.json", ads)
    provisional = _write_provisional(
        tmp_path / "provisional.json", artifact, ads, "run-1"
    )
    final = tmp_path / "final.json"
    db = tmp_path / "watch.db"
    _seed_existing(db, artifact, ads)
    importer.import_artifact(
        db, artifact, min_total=1, min_prices=1,
        collection_manifest=provisional, source_manifest_out=final,
    )

    provisional = _write_provisional(
        tmp_path / "provisional.json", artifact, ads, "run-2"
    )
    importer.import_artifact(
        db, artifact, min_total=1, min_prices=1,
        collection_manifest=provisional, source_manifest_out=final,
    )

    manifest = SourceRunManifest.from_dict(
        json.loads(final.read_text(encoding="utf-8"))
    )
    assert manifest.inserted == 0
    assert manifest.reappeared == 0
    assert manifest.withdrawn == 1
    con = sqlite3.connect(db)
    assert con.execute(
        "SELECT is_active FROM rental_listings WHERE source_id='gone'"
    ).fetchone()[0] == 0
    con.close()


def test_import_accepts_proven_empty_snapshot_and_keeps_final_manifest_complete(
    tmp_path: Path,
):
    artifact = _write_artifact(tmp_path / "seloger-empty.json", [])
    provisional = _write_provisional(
        tmp_path / "provisional.json", artifact, [], "run-empty"
    )
    final = tmp_path / "final.json"
    db = tmp_path / "watch.db"

    result = importer.import_artifact(
        db,
        artifact,
        collection_manifest=provisional,
        source_manifest_out=final,
    )

    manifest = SourceRunManifest.from_dict(
        json.loads(final.read_text(encoding="utf-8"))
    )
    assert result["artifact_total"] == 0
    assert manifest.status == "complete"
    assert manifest.expected_count == 0
    assert manifest.inserted == 0
    assert manifest.withdrawn == 0

def test_partial_manifest_is_a_database_noop_and_writes_no_final_manifest(
    tmp_path: Path,
):
    ads = [_ad("new"), _ad("reappear"), _ad("seen")]
    artifact = _write_artifact(tmp_path / "seloger.json", ads)
    provisional = _write_provisional(
        tmp_path / "provisional.json", artifact, ads, "partial-run"
    )
    payload = json.loads(provisional.read_text(encoding="utf-8"))
    payload.update(
        status="partial",
        truncation_signals=["page_cap:1"],
        terminal_reason="page_cap",
    )
    provisional.write_text(json.dumps(payload), encoding="utf-8")
    final = tmp_path / "final.json"
    db = tmp_path / "watch.db"
    _seed_existing(db, artifact, ads)
    con = sqlite3.connect(db)
    before = con.execute(
        "SELECT source_id,is_active,seen_last_at FROM rental_listings ORDER BY source_id"
    ).fetchall()
    con.close()

    with pytest.raises(RuntimeError, match="not complete: partial"):
        importer.import_artifact(
            db, artifact, min_total=1, min_prices=1,
            collection_manifest=provisional, source_manifest_out=final,
        )

    con = sqlite3.connect(db)
    after = con.execute(
        "SELECT source_id,is_active,seen_last_at FROM rental_listings ORDER BY source_id"
    ).fetchall()
    con.close()
    assert after == before
    assert not final.exists()


def test_partial_manifest_can_be_imported_in_degraded_mode_without_withdrawals(
    tmp_path: Path,
):
    ads = [_ad("new"), _ad("reappear"), _ad("seen")]
    artifact = _write_artifact(tmp_path / "seloger.json", ads)
    provisional = _write_provisional(
        tmp_path / "provisional.json", artifact, ads, "partial-run"
    )
    payload = json.loads(provisional.read_text(encoding="utf-8"))
    payload.update(
        status="partial",
        truncation_signals=["repeated_page"],
        terminal_reason="repeated_page",
    )
    provisional.write_text(json.dumps(payload), encoding="utf-8")
    final = tmp_path / "final.json"
    db = tmp_path / "watch.db"
    _seed_existing(db, artifact, ads)

    result = importer.import_artifact(
        db,
        artifact,
        min_total=1,
        min_prices=1,
        collection_manifest=provisional,
        source_manifest_out=final,
        allow_partial_manifest=True,
        mark_inactive=True,
    )

    manifest = SourceRunManifest.from_dict(json.loads(final.read_text(encoding="utf-8")))
    assert manifest.status == "partial"
    assert manifest.withdrawn == 0
    assert result["inactive_marked"] == 0
    con = sqlite3.connect(db)
    assert con.execute(
        "SELECT is_active FROM rental_listings WHERE source_id='gone'"
    ).fetchone()[0] == 1
    assert con.execute(
        "SELECT is_active FROM rental_listings WHERE source_id='new'"
    ).fetchone()[0] == 1
    con.close()


def test_replaying_same_complete_run_does_not_advance_absence_ledger(
    tmp_path: Path,
):
    ads = [_ad("new"), _ad("reappear"), _ad("seen")]
    artifact = _write_artifact(tmp_path / "seloger.json", ads)
    provisional = _write_provisional(
        tmp_path / "provisional.json", artifact, ads, "same-run"
    )
    final = tmp_path / "final.json"
    db = tmp_path / "watch.db"
    _seed_existing(db, artifact, ads)

    for _ in range(2):
        importer.import_artifact(
            db, artifact, min_total=1, min_prices=1,
            collection_manifest=provisional, source_manifest_out=final,
        )

    manifest = SourceRunManifest.from_dict(json.loads(final.read_text(encoding="utf-8")))
    assert manifest.withdrawn == 0
    con = sqlite3.connect(db)
    assert con.execute(
        """SELECT successful_missing_runs FROM source_absence_state
        WHERE source_site='seloger' AND source_id='gone'"""
    ).fetchone()[0] == 1
    assert con.execute(
        "SELECT is_active FROM rental_listings WHERE source_id='gone'"
    ).fetchone()[0] == 1
    con.close()

def test_complete_collection_requires_final_manifest_path_before_database_write(
    tmp_path: Path,
):
    ads = [_ad("new")]
    artifact = _write_artifact(tmp_path / "seloger.json", ads)
    provisional = _write_provisional(
        tmp_path / "provisional.json", artifact, ads, "run-no-final"
    )
    db = tmp_path / "watch.db"

    with pytest.raises(RuntimeError, match="source_manifest_out is required"):
        importer.import_artifact(
            db, artifact, min_total=1, min_prices=1,
            collection_manifest=provisional,
        )

    assert not db.exists()
