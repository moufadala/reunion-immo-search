#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_public_delta_guard.py"


def write_app(path: Path, rows: list[dict]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (path / "listings.json").write_text(json.dumps({"listings": rows}), encoding="utf-8")


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, text=True, capture_output=True, timeout=30)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="delta-guard-test-") as td:
        tmp = Path(td)
        baseline = tmp / "baseline"
        candidate_ok = tmp / "candidate_ok"
        candidate_bad = tmp / "candidate_bad"
        rows = [{"source": "seloger", "id": i} for i in range(40)] + [{"source": "ofim", "id": i} for i in range(60)]
        write_app(baseline, rows)
        write_app(candidate_ok, rows[:92])
        write_app(candidate_bad, rows[:50])

        ok = run(["--baseline", str(baseline), "--candidate", str(candidate_ok)], ROOT)
        assert ok.returncode == 0, ok.stdout + ok.stderr
        bad = run(["--baseline", str(baseline), "--candidate", str(candidate_bad)], ROOT)
        assert bad.returncode != 0, bad.stdout + bad.stderr
        assert "global volume dropped" in (bad.stdout + bad.stderr)

        candidate_critical_bad = tmp / "candidate_critical_bad"
        critical_bad_rows = [{"source": "seloger", "id": i} for i in range(20)] + [{"source": "ofim", "id": i} for i in range(80)]
        write_app(candidate_critical_bad, critical_bad_rows)
        bad = run(["--baseline", str(baseline), "--candidate", str(candidate_critical_bad), "--max-drop-pct", "99"], ROOT)
        assert bad.returncode != 0, bad.stdout + bad.stderr
        assert "critical source seloger dropped" in (bad.stdout + bad.stderr)
    print("AUDIT_PUBLIC_DELTA_GUARD_TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
