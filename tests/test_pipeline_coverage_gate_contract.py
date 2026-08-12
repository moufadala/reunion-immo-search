from pathlib import Path


def test_publication_gate_consumes_listing_level_coverage_failures():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")

    assert "coverage_below_threshold" in script
    assert "coverage-low" in script
    assert "coverage_low" in script
    assert "source coverage below threshold" in script


def test_photo_stages_hardlink_existing_media_before_downloads():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    hardlink_export = script.index("export IMMO_MEDIA_COPY_MODE=hardlink")
    seed = script.index("run_step seed_photo_cache")
    assert hardlink_export < seed
    assert "cp -an" not in script


def test_db_drop_override_is_explicit_and_defaults_to_strict_gate():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    assert '--max-drop-pct "${IMMO_MAX_DB_DROP_PCT:-15}"' in script
    assert "IMMO_MAX_DB_DROP_PCT:-20" not in script
    assert "IMMO_MAX_DB_DROP_PCT:-100" not in script
