from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_history_runs_before_changes_and_product_export():
    script = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    history = script.index("run_step listing_history_canonical")
    promote_db = script.index("run_step promote_db_candidate")
    changes = script.index("run_step listing_changes")
    product = script.index("run_step build_product_v2")
    postflight = script.index("run_step postflight_public_contract")
    promote_history = script.index("run_step promote_listing_history")
    keep = script.index("APP_KEEP=1")

    assert "--source-db \"$PROD_DB\" --db \"$HISTORY_STAGE\"" in script
    assert 'export IMMO_EVENTS_DB="$HISTORY_STAGE"' in script
    assert promote_db < history < changes < product
    assert 'src/listing_changes.py" --db "$HISTORY_STAGE"' in script
    assert product < postflight < promote_history < keep


def test_post_refresh_notifier_does_not_mutate_history_after_gates():
    script = (ROOT / "scripts" / "reunion_watch_post_refresh_alerts.sh").read_text(encoding="utf-8")

    assert "python3 src/listing_history.py" not in script
