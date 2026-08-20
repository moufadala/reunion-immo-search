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

    assert "--source-db \"$DB\" --db \"$HISTORY_STAGE\"" in script
    assert 'export IMMO_EVENTS_DB="$HISTORY_STAGE"' in script
    assert history < changes < product < promote_db
    assert 'src/listing_changes.py" --db "$HISTORY_STAGE"' in script
    assert product < promote_db < promote_history < postflight < keep


def test_post_refresh_notifier_does_not_mutate_history_after_gates():
    script = (ROOT / "scripts" / "reunion_watch_post_refresh_alerts.sh").read_text(encoding="utf-8")

    assert "python3 src/listing_history.py" not in script


def test_pipeline_gates_unexplained_description_gaps_before_product_build():
    script = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    gate_index = script.index("run_step description_observability_gate")
    build_index = script.index("run_step build_technical_app")

    assert "-m src.description_observability" in script
    assert '--db "$DB"' in script
    assert gate_index < build_index
