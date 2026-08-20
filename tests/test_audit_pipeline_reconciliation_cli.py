from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_pipeline_reconciliation.py"


def test_cli_writes_machine_readable_failure_and_returns_nonzero(tmp_path):
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    source.write_text(json.dumps({"run_id": "run-1", "sources": []}), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--out", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "no source manifests" in result["errors"]
