from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_feed_keeps_transitions_separate_from_active_inventory() -> None:
    source = (ROOT / "scripts" / "export_feed.py").read_text(encoding="utf-8")
    assert "def load_movement_events" in source
    assert "'events': movement_events" in source
    assert source.index("movements = {") < source.index("listings = active_public_listings(listings)")


def test_movement_page_consumes_event_payload_not_inactive_cards() -> None:
    app = (ROOT / "webapp" / "src" / "App.jsx").read_text(encoding="utf-8")
    component = (ROOT / "webapp" / "src" / "components" / "Mouvements.jsx").read_text(
        encoding="utf-8"
    )
    assert "movements={data?.movements}" in app
    assert 'e.event_type === "disappeared"' in component
    assert 'e.event_type === "reappeared"' in component
    assert "revenues: []" not in component
