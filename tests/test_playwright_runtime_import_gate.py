from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tests" / "audit_runtime_playwright_import.py"
DAILY = ROOT / "scripts" / "immo_daily_public_refresh.sh"


def _run_gate(env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [str(ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")), str(GATE)],
        cwd=ROOT,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def test_runtime_playwright_imports_are_from_project_venv() -> None:
    proc = _run_gate()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["errors"] == []
    assert Path(payload["prefix"]).resolve() == (ROOT / ".venv").resolve()
    assert "/.local/lib/python3.13/site-packages" not in "\n".join(payload["path"])
    assert str(ROOT / ".venv") in payload["greenlet"]
    assert str(ROOT / ".venv") in payload["greenlet._greenlet"]
    assert str(ROOT / ".venv") in payload["playwright"]
    assert str(ROOT / ".venv") in payload["playwright.sync_api"]


def test_runtime_gate_rejects_python313_user_site_injection() -> None:
    bad_user_site = "/opt/data/home/.local/lib/python3.13/site-packages"
    proc = _run_gate({"PYTHONPATH": bad_user_site})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert any("foreign Python user-site" in error for error in payload["errors"])


def test_daily_refresh_sanitizes_python_import_environment_before_steps() -> None:
    script = DAILY.read_text(encoding="utf-8")
    export_pos = script.index("export PYTHONNOUSERSITE=1")
    unset_pos = script.index("unset PYTHONPATH")
    project_pos = script.index('PROJECT="${IMMO_PROJECT_DIR:-/opt/data/projects/reunion-immo-search}"')
    assert export_pos < project_pos
    assert unset_pos < project_pos
    assert "if [ ! -x \"$PY\" ]; then PY=python3; fi" not in script
    assert "IMMO_PROJECT_PYTHON is not executable" in script
