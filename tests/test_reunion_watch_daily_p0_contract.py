from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / "scripts" / "reunion_watch_daily.sh"


def test_daily_script_builds_p0_edition_even_when_full_immo_refresh_fails() -> None:
    source = DAILY.read_text(encoding="utf-8")

    assert "IMMO_REFRESH_RC=0" in source
    assert "build_daily_edition.py" in source
    assert "IMMO_DAILY_EDITION_JSON" in source

    refresh_call = source.index("immo_daily_public_refresh.sh")
    edition_call = source.index("build_daily_edition.py")
    assert refresh_call < edition_call

    old_abort = 'echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] CRITICAL immo_daily_public_refresh failed rc=$rc json=$IMMO_REFRESH_JSON" >>"$PIPELINE_LOG"\n    exit "$rc"'
    assert old_abort not in source

