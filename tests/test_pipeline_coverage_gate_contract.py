from pathlib import Path


def test_publication_gate_consumes_listing_level_coverage_failures():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")

    assert "coverage_below_threshold" in script
    assert "coverage-low" in script
    assert "coverage_low" in script
    assert "source coverage below threshold" in script
