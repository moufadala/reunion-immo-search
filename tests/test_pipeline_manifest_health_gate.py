from __future__ import annotations

from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "immo_daily_public_refresh.sh"
).read_text(encoding="utf-8")


def test_exact_fourteen_source_manifest_gate_runs_before_db_promotion():
    bundle = SCRIPT.index("run_step source_manifest_bundle")
    health = SCRIPT.index("run_step stage_source_freshness_gate")
    promotion = SCRIPT.index("run_step promote_db_candidate")

    assert bundle < health < promotion
    assert '"$RUN_DIR/source_run_manifests.json"' in SCRIPT


def test_historic_coverage_is_diagnostic_after_current_snapshot_is_proven():
    start = SCRIPT.index("run_step stage_source_freshness_gate")
    end = SCRIPT.index("if [ \"$STAGE_DB_MODE\"", start)
    gate = SCRIPT[start:end]

    assert "manifest_gate" in gate
    assert "health_warnings" in gate
    assert "source coverage below threshold" in gate
    assert "problems.append(f'source coverage below threshold" not in gate
