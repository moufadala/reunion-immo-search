from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_v2_app_exposes_stable_accessible_browser_contract():
    app = (ROOT / "webapp/src/App.jsx").read_text(encoding="utf-8")
    card = (ROOT / "webapp/src/components/Card.jsx").read_text(encoding="utf-8")
    movements = (ROOT / "webapp/src/components/Mouvements.jsx").read_text(encoding="utf-8")
    assert 'data-testid="app-root"' in app
    assert 'data-testid="listing-count"' in app
    assert 'data-testid="listings-grid"' in app
    assert 'aria-label="Navigation principale"' in app
    assert 'aria-current={onglet === o.id ? "page" : undefined}' in app
    assert 'aria-label="Trier les annonces"' in app
    assert 'data-testid="listing-card"' in card
    assert 'data-listing-id={l.id}' in card
    assert 'age >= 0 && age <= 7 * 86400000' in movements
    assert 'key={l.event_id ||' in movements
    assert '<button type="button" onClick={() => onOuvrir(l)}' in card
    assert 'role="button"' not in card and 'tabIndex={0}' not in card
    for testid in ("movement-online", "movement-new", "movement-withdrawn", "movement-reappeared"):
        assert f'data-testid="{testid}"' in movements
    for event_type in ("new", "disappeared", "reappeared"):
        assert f'eventType="{event_type}"' in movements


def test_browser_audits_target_v2_root_and_avoid_legacy_dom():
    user = (ROOT / "tests/audit_user_search_cases.py").read_text(encoding="utf-8")
    changes = (ROOT / "tests/audit_changes_page_filters.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "scripts/immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    legacy = ("#total", "#summary", "#nlChips", ".card", ".change-card", "#countLabel", "changes.json")
    for selector in legacy:
        assert selector not in user
        assert selector not in changes
    assert 'get_by_test_id("listing-count")' in user
    assert 'page.route("**/feed.json"' in user
    assert 'expected_sorted_ids' in user
    assert 'get_by_test_id("movement-online")' in changes
    assert 'IMMO_PUBLIC_URL="http://127.0.0.1:$LOCAL_AUDIT_PORT/"' in pipeline
    assert 'IMMO_CHANGES_URL=' not in pipeline
