from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")


def test_full_candidate_is_built_and_gated_before_any_public_swap() -> None:
    history = SCRIPT.index("run_step listing_history_canonical")
    product = SCRIPT.index("run_step build_product_v2_candidate")
    reconciliation = SCRIPT.index("run_step pipeline_reconciliation_candidate")
    product_gate = SCRIPT.index("run_step product_v2_candidate_gate")
    begin = SCRIPT.index("run_step global_publication_begin")
    promote_db = SCRIPT.index("run_step promote_db_candidate")
    promote_app = SCRIPT.index("run_step promote_app_candidate")

    assert '--source-db "$DB" --db "$HISTORY_STAGE"' in SCRIPT
    assert 'IMMO_APP_PATH="$CLEAN_STAGE"' in SCRIPT
    assert 'IMMO_DB_PATH="$DB"' in SCRIPT
    assert 'IMMO_EVENTS_DB="$HISTORY_STAGE"' in SCRIPT
    assert history < product < reconciliation < product_gate < begin
    assert begin < promote_db < promote_app
    assert "run_step build_product_v2 " not in SCRIPT[promote_app:]


def test_global_transaction_survives_until_summary_is_durable() -> None:
    recover = SCRIPT.index("run_step global_publication_recover")
    begin = SCRIPT.index("run_step global_publication_begin")
    promote_history = SCRIPT.index("run_step promote_listing_history")
    postflight = SCRIPT.index("run_step postflight_public_contract")
    summary = SCRIPT.index("final_summary.json")
    commit = SCRIPT.index("run_step global_publication_commit")
    keep = SCRIPT.index("APP_KEEP=1")

    assert recover < begin < promote_history < postflight < summary < commit < keep
    assert 'pipeline_publication_transaction.py" recover' in SCRIPT
    assert 'pipeline_publication_transaction.py" begin' in SCRIPT
    assert 'pipeline_publication_transaction.py" commit' in SCRIPT
    trap = SCRIPT[SCRIPT.index("restore_on_failure()") : SCRIPT.index("trap restore_on_failure EXIT")]
    assert "pipeline_publication_transaction.py" in trap
    assert "recover" in trap
