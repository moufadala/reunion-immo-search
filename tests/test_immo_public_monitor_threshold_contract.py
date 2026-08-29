from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "immo_public_monitor.py").read_text(encoding="utf-8")


def test_seloger_public_monitor_threshold_matches_current_public_scope() -> None:
    assert 'IMMO_PUBLIC_MONITOR_MIN_SELOGER' in SCRIPT
    assert '"3"' in SCRIPT
    assert 'stricter public content contract' in SCRIPT
    assert 'public feed actually served' in SCRIPT
