from datetime import datetime, timezone
import sqlite3
import sys

from pathlib import Path

from scripts.detail_enrich import description_fields, process
import scripts.detail_enrich as detail_enrich


def test_detail_enrichment_preserves_existing_values_and_extra_columns():
    source = (Path(__file__).parents[1] / "scripts" / "detail_enrich.py").read_text(
        encoding="utf-8"
    ).lower()

    assert "insert or replace into listing_detail" not in source
    assert "on conflict(source_site, source_id) do update" in source
    assert "description_full=coalesce(nullif(trim(excluded.description_full), '')" in source
    assert "photo_urls=" not in source.split("on conflict(source_site, source_id) do update", 1)[1]


def test_detail_enrichment_persists_description_attempt_evidence():
    source = (Path(__file__).parents[1] / "scripts" / "detail_enrich.py").read_text(
        encoding="utf-8"
    )

    for column in (
        "description_content_state",
        "description_length",
        "description_sha256",
        "description_attempt_status",
    ):
        assert column in source
        "description_attempted_at",
        "description_attempt_length",
        "description_attempt_sha256",
        "description_extractor_version",


def test_description_fields_preserve_existing_complete_text_after_empty_200():
    existing = (
        "Appartement lumineux avec trois chambres, grande varangue, parking securise "
        "et cuisine equipee dans une residence calme."
    )
    attempted_at = datetime(2026, 8, 18, 9, tzinfo=timezone.utc).isoformat()
    fields = description_fields(
        existing, "", attempted_at=attempted_at, http_status=200,
        existing_succeeded_at="2026-08-17T09:00:00+00:00",
    )
    assert fields["description_full"] == existing
    assert fields["description_attempt_status"] == "fetched_sparse"
    assert fields["description_attempt_length"] == 0
    assert fields["description_succeeded_at"] == "2026-08-17T09:00:00+00:00"


def test_description_fields_accept_legitimate_short_source_text():
    attempted_at = datetime(2026, 8, 18, 9, tzinfo=timezone.utc).isoformat()
    fields = description_fields(
        "", "Studio meuble, libre immediatement.",
        attempted_at=attempted_at, http_status=200, full_text_evidence=True,
    )
    assert fields["description_attempt_status"] == "source_short_complete"
    assert fields["description_content_state"] == "source"
    assert fields["description_succeeded_at"] == attempted_at
    assert fields["description_full_text_evidence"] == 1


def test_process_separates_detail_candidate_from_listing_fallback():
    fallback = "Description de vignette suffisamment longue pour rester visible meme si le detail est vide."
    result = process("<html><body>Aucun descriptif ici</body></html>", fallback)

    assert result["description_full"] == fallback
    assert result["_description_attempt_candidate"] == ""


def test_process_retains_legitimate_short_detail_candidate():
    text = "Studio meuble, libre immediatement."
    result = process(
        '<html><body><div class="description">%s</div></body></html>' % text,
        "",
    )

    assert result["description_full"] == text
    assert result["_description_attempt_candidate"] == text
    assert result["_description_full_text_evidence"] is True


def test_process_prefers_structural_detail_over_longer_seo_meta():
    detail = "Appartement T3 avec balcon et parking."
    seo = (
        "Appartement T3 avec balcon et parking. Consultez toutes nos annonces, "
        "nos agences et nos services immobiliers a La Reunion."
    )
    page = (
        f'<meta property="og:description" content="{seo}">'
        f'<section class="description">{detail}</section>'
    )

    result = process(page, "")

    assert result["_description_attempt_candidate"] == detail
    assert result["_description_full_text_evidence"] is True


def test_process_marks_meta_only_description_as_unverified():
    seo = "Appartement T3 avec balcon et parking, proche de toutes les commodites."
    result = process(f'<meta name="description" content="{seo}">', "")

    assert result["_description_attempt_candidate"] == seo
    assert result["_description_full_text_evidence"] is False




def test_main_executes_upsert_with_attempt_evidence(tmp_path, monkeypatch):
    db_path = tmp_path / "watch.sqlite"
    cache_path = tmp_path / "cache"
    con = sqlite3.connect(db_path)
    con.execute(
        """CREATE TABLE rental_listings (
        source_site TEXT, source_id TEXT, url TEXT, description TEXT,
        is_active INTEGER, seen_last_at TEXT
        )"""
    )
    fallback = (
        "Ancienne description source complete avec trois chambres, une varangue, "
        "un parking et une cuisine equipee."
    )
    con.execute(
        "INSERT INTO rental_listings VALUES (?,?,?,?,?,?)",
        ("portal", "p1", "https://example.test/p1", fallback, 1, "2026-08-18"),
    )
    con.commit()
    con.close()

    monkeypatch.setattr(detail_enrich, "DB", str(db_path))
    monkeypatch.setattr(detail_enrich, "CACHE", str(cache_path))
    monkeypatch.setattr(
        detail_enrich,
        "fetch",
        lambda _url: (200, '<div class="description">Studio meuble, libre immediatement.</div>'),
    )
    monkeypatch.setattr(sys, "argv", ["detail_enrich.py", "--all", "--delay", "0"])

    detail_enrich.main()

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT description_full,description_attempt_status,description_attempt_length,"
        "description_attempted_at,description_extractor_version FROM listing_detail"
    ).fetchone()
    con.close()
    assert row[0] == fallback
    assert row[1] == "source_short_complete"
    assert row[2] == len("Studio meuble, libre immediatement.")
    assert row[3]
    assert row[4] == detail_enrich.DESCRIPTION_EXTRACTOR_VERSION


def test_completion_stats_and_skip_require_verified_detail_evidence():
    source = (Path(__file__).parents[1] / "scripts" / "detail_enrich.py").read_text(
        encoding="utf-8"
    )
    stats_body = source.split("if a.stats:", 1)[1].split("q = (", 1)[0]
    skip_body = source.split("if not a.redo:", 1)[1].split("if a.only_active:", 1)[0]

    for body in (stats_body, skip_body):
        assert "description_full_text_evidence=1" in body
        assert "description_attempt_status in" in body
        assert "length(trim(coalesce(description_full" not in body


def test_network_and_cache_paths_persist_description_evidence_not_only_schema():
    source = (Path(__file__).parents[1] / "scripts" / "detail_enrich.py").read_text(
        encoding="utf-8"
    )
    reparse_body = source.split("def reparse(c):", 1)[1].split("def main():", 1)[0]
    network_body = source.split("for i, (ss, si, url, fb)", 1)[1]
    assert "description_fields(" in reparse_body
    assert "description_fields(" in network_body
    for assignment in (
        "description_content_state=excluded.description_content_state",
        "description_attempt_status=excluded.description_attempt_status",
        "description_attempted_at=excluded.description_attempted_at",
        "description_attempt_sha256=excluded.description_attempt_sha256",
    ):
        assert assignment in network_body
