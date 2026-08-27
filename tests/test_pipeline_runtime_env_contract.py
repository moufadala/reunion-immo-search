from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_daily_pipeline_isolates_project_python_from_user_site() -> None:
    source = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    assert "unset PYTHONPATH PYTHONHOME" in source
    assert "export PYTHONNOUSERSITE=1" in source


def test_browser_import_gate_runs_before_expensive_scrapers() -> None:
    source = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    assert "run_step browser_runtime_preflight" in source


def test_daily_pipeline_can_be_bound_to_agent_os_worktree() -> None:
    source = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    assert 'PROJECT="${IMMO_PROJECT_DIR:-/opt/data/projects/reunion-immo-search}"' in source
    assert "export IMMO_PROJECT=\"$PROJECT\"" in source
    assert "IMMO_PROJECT_DIR is not a reunion-immo-search checkout" in source


def test_candidate_gate_runs_before_any_public_promotion() -> None:
    source = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    candidate_gate_pos = source.index("run_step public_monitor_candidate_gate")
    candidate_only_pos = source.index('if [ "${IMMO_CANDIDATE_ONLY:-0}" = "1" ]')
    app_promote_pos = source.index("run_step promote_app_candidate")
    db_promote_pos = source.index("run_step promote_db_candidate")
    publish_pos = source.index("run_step publish_clean_static")
    assert candidate_gate_pos < candidate_only_pos < db_promote_pos < app_promote_pos < publish_pos
    assert '"promoted": False' in source or "'promoted': False" in source
