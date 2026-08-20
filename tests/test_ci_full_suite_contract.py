from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_github_ci_runs_for_stabilization_branch_with_locked_full_suite():
    workflow = (ROOT / ".github" / "workflows" / "product-qa.yml").read_text(
        encoding="utf-8"
    )

    assert "'stabilize/**'" in workflow
    assert "astral-sh/setup-uv@" in workflow
    assert "uv sync --locked" in workflow
    assert "uv run pytest -q" in workflow
    # pytest.ini excludes this portable source gate from the default suite, so CI
    # must run it explicitly to keep the fourteen-source publication gate covered.
    assert "uv run pytest -q tests/test_realestate_watch_source_gate.py" in workflow
    assert "tests/test_leboncoin_apify_mapping.py \\" not in workflow
    assert "npm audit --audit-level=high" in workflow
    assert "npm run build" in workflow
