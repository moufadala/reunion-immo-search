from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_daily_pipeline_isolates_project_python_from_user_site() -> None:
    source = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    assert "unset PYTHONPATH PYTHONHOME" in source
    assert "export PYTHONNOUSERSITE=1" in source


def test_browser_import_gate_runs_before_expensive_scrapers() -> None:
    source = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    assert "run_step browser_runtime_preflight" in source
