from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "seloger_multi_page.py").read_text(encoding="utf-8")


def test_seloger_cdp_uses_deterministic_page_url_not_react_click_only() -> None:
    assert "BASE_URL if page_num == 1 else f'{BASE_URL}&page={page_num}'" in SCRIPT
    assert "page.goto(url" in SCRIPT


def test_seloger_still_has_repeated_page_guard() -> None:
    assert "terminal_reason = 'repeated_page'" in SCRIPT
    assert "if new_count == 0:" in SCRIPT
