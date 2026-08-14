from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cards_do_not_present_last_scrape_as_listing_age() -> None:
    source = (ROOT / "webapp/src/components/Card.jsx").read_text(encoding="utf-8")
    assert 'récupérée "}{ilYA(l.seen_last)' not in source
    assert "l.published" in source
    assert "l.seen_first" in source


def test_detail_shows_source_publication_when_available() -> None:
    source = (ROOT / "webapp/src/components/Detail.jsx").read_text(encoding="utf-8")
    assert "l.published" in source
