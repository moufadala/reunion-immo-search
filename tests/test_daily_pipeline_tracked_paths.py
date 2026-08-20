from __future__ import annotations

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / "scripts" / "immo_daily_public_refresh.sh"
REPO_SCRIPT = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:\$PROJECT/)?"
    r"((?:scripts|src|tests|deploy)/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:py|sh))"
)


def test_every_repo_script_invoked_by_daily_pipeline_exists_and_is_git_tracked() -> None:
    source = DAILY.read_text(encoding="utf-8")
    invoked = sorted(set(REPO_SCRIPT.findall(source)))
    tracked_proc = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.resolve()}", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
    )
    assert tracked_proc.returncode == 0, tracked_proc.stderr.decode("utf-8", errors="replace")
    tracked = set(tracked_proc.stdout.decode("utf-8").split("\0"))

    assert "scripts/qa_public_external_v2.py" in invoked
    assert invoked, "daily pipeline must expose literal, portable repo script paths"
    assert [path for path in invoked if not (ROOT / path).is_file()] == []
    assert [path for path in invoked if path not in tracked] == []


def test_bienici_runtime_script_executes_from_repository() -> None:
    watcher = (ROOT / "scripts" / "realestate_watch.py").read_text(encoding="utf-8")
    audit = (ROOT / "tests" / "audit_pipeline_script_contracts.py").read_text(encoding="utf-8")

    assert "BIENICI_SCRAPER = PROJECT_ROOT / 'scripts/bienici_rental_scraper.py'" in watcher
    assert "'source': 'bienici', 'script': BIENICI_SCRAPER" in watcher
    assert '("bienici_rental_scraper.py", "scripts/bienici_rental_scraper.py")' not in audit
