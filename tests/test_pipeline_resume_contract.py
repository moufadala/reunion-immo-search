from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")


def test_daily_pipeline_can_skip_completed_steps_on_explicit_resume() -> None:
    assert 'RESUME_COMPLETED="${IMMO_RESUME_COMPLETED:-0}"' in SCRIPT
    assert 'skipped=resume_completed' in SCRIPT
    assert 'grep -Eq "(^| )rc=0($| )" "$status"' in SCRIPT


def test_resume_is_opt_in_not_default() -> None:
    resume_default = SCRIPT.index('RESUME_COMPLETED="${IMMO_RESUME_COMPLETED:-0}"')
    first_step = SCRIPT.index('run_step global_publication_recover')
    assert resume_default < first_step
    assert 'IMMO_RESUME_COMPLETED:-0' in SCRIPT


def test_report_only_steps_use_same_resume_checkpoint_contract() -> None:
    run_step = SCRIPT.index('run_step()')
    report_step = SCRIPT.index('report_step()')
    assert SCRIPT.count('skipped=resume_completed') >= 2
    assert run_step < report_step < SCRIPT.index('run_step global_publication_recover')


def test_stage_directories_are_not_deleted_on_resume() -> None:
    resume_flag = SCRIPT.index('RESUME_COMPLETED="${IMMO_RESUME_COMPLETED:-0}"')
    stage_decl = SCRIPT.index('TECH_STAGE="$PROJECT/artifacts/daily-tech-stage-${STAMP}"')
    guarded_rm = SCRIPT.index('if [ "$RESUME_COMPLETED" != "1" ]; then\n  rm -rf "$TECH_STAGE" "$CLEAN_STAGE"')
    build_step = SCRIPT.index('run_step build_technical_app')
    assert resume_flag < stage_decl < guarded_rm < build_step
