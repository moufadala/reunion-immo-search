from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_v2_app_exposes_stable_accessible_browser_contract():
    app = (ROOT / "webapp/src/App.jsx").read_text(encoding="utf-8")
    card = (ROOT / "webapp/src/components/Card.jsx").read_text(encoding="utf-8")
    movements = (ROOT / "webapp/src/components/Mouvements.jsx").read_text(encoding="utf-8")
    assert 'data-testid="app-root"' in app
    assert 'data-testid="listing-count"' in app
    assert 'data-testid="listings-grid"' in app
    assert 'data-testid="sources-panel"' in app
    assert 'fetch("source_health.json"' in app
    assert 'data-testid="source-health-summary"' in app
    assert 'data-testid="source-health-error"' in app
    assert 'data-testid="source-portal-row"' in app
    assert 'data-testid="source-auxiliary-row"' in app
    assert "PORTAILS_ATTENDUS" in app
    assert "const BLOQUES" not in app
    assert "DataDome" not in app
    assert 'aria-label="Navigation principale"' in app
    assert 'aria-current={onglet === o.id ? "page" : undefined}' in app
    assert 'aria-label="Trier les annonces"' in app
    assert 'data-testid="listing-card"' in card
    assert 'data-listing-id={l.id}' in card
    assert 'age >= 0 && age <= 7 * 86400000' in movements
    assert 'key={l.event_id ||' in movements
    assert 'onClick={() => onOuvrir(l)}' in card
    assert 'data-testid="card-photo"' in card
    assert 'aria-label="Photo précédente"' in card
    assert 'aria-label="Photo suivante"' in card
    assert 'e.stopPropagation()' in card
    assert 'cursor-pointer' in card
    assert 'role="button"' not in card and 'tabIndex={0}' not in card
    for testid in ("movement-online", "movement-new", "movement-withdrawn", "movement-reappeared"):
        assert f'data-testid="{testid}"' in movements
    for event_type in ("new", "disappeared", "reappeared"):
        assert f'eventType="{event_type}"' in movements


def test_header_names_the_exact_two_commune_scope():
    app = (ROOT / "webapp/src/App.jsx").read_text(encoding="utf-8")

    assert "Saint-Denis + Sainte-Marie" in app
    assert "Nord &amp; Est" not in app


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
    assert 'page.route("**/source_health.json"' in user
    assert 'SOURCE_HEALTH_FIXTURE' in user
    assert 'get_by_test_id("source-health-summary")' in user
    assert 'get_by_test_id("source-health-error")' in user
    assert 'get_by_test_id("source-portal-row")' in user
    assert 'get_by_test_id("source-auxiliary-row")' in user
    assert 'expected_sorted_ids' in user
    assert 'get_by_test_id("movement-online")' in changes
    assert 'IMMO_PUBLIC_URL="http://127.0.0.1:$LOCAL_AUDIT_PORT/"' in pipeline
    assert 'IMMO_CHANGES_URL=' not in pipeline
