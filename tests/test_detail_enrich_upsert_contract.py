from pathlib import Path


def test_detail_enrichment_preserves_existing_values_and_extra_columns():
    source = (Path(__file__).parents[1] / "scripts" / "detail_enrich.py").read_text(
        encoding="utf-8"
    ).lower()

    assert "insert or replace into listing_detail" not in source
    assert "on conflict(source_site, source_id) do update" in source
    assert "description_full=coalesce(nullif(trim(excluded.description_full), '')" in source
    assert "photo_urls=" not in source.split("on conflict(source_site, source_id) do update", 1)[1]
