from pathlib import Path


def test_resolver_uses_installed_chromium_from_configured_roots(tmp_path, monkeypatch):
    from src.browser_qa_runtime import resolve_browser_executable

    missing = tmp_path / "missing"
    installed = tmp_path / "home-cache" / "chromium-1223" / "chrome-linux64" / "chrome"
    installed.parent.mkdir(parents=True)
    installed.touch()
    monkeypatch.delenv("IMMO_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.setenv("IMMO_BROWSER_SEARCH_ROOTS", f"{missing}{__import__('os').pathsep}{installed.parents[2]}")

    assert resolve_browser_executable() == installed


def test_explicit_browser_executable_is_authoritative(tmp_path, monkeypatch):
    from src.browser_qa_runtime import resolve_browser_executable

    executable = tmp_path / "chrome"
    executable.touch()
    monkeypatch.setenv("IMMO_BROWSER_EXECUTABLE", str(executable))
    assert resolve_browser_executable() == executable


def test_pipeline_browser_contract_is_blocking_and_shared():
    pipeline = Path("scripts/immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    preflight = pipeline.index("browser_qa_preflight")
    user_audit = pipeline.index("run_step public_user_search_audit")
    changes_audit = pipeline.index("run_step public_changes_filter_audit")
    assert preflight < user_audit < changes_audit


def test_browser_audits_use_shared_runtime():
    for path in (Path("tests/audit_user_search_cases.py"), Path("tests/audit_changes_page_filters.py")):
        source = path.read_text(encoding="utf-8")
        assert "launch_chromium(p)" in source
        assert "p.chromium.launch(" not in source
