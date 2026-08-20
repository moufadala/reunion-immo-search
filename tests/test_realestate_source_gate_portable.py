from __future__ import annotations

from scripts import realestate_watch


def _result(source: str, ok: bool) -> realestate_watch.RunnerResult:
    return realestate_watch.RunnerResult(
        script=f"{source}.py",
        ok=ok,
        exit_code=0,
        stdout_path="",
        stderr_path="",
        parsed_summary={},
        source=source,
        duration_sec=1.0,
        timeout_sec=300,
        status_path=f"/{source}.status.json",
    )


def test_thirteen_of_fourteen_sources_block_when_critical_source_failed():
    sources = ["bienici", "ofim", "97immo", "adrezio", "alter", "citya", "domimmo", "fnaim", "immo974", "locamoi", "ofim_rss", "superimmo", "zimo"]
    results = [_result(source, True) for source in sources] + [_result("leboncoin", False)]

    gate = realestate_watch.evaluate_source_gate(results)

    assert gate["ok"] is False
    assert gate["ok_count"] == 13
    assert gate["total"] == 14
    assert gate["failed_sources"] == ["leboncoin"]


def test_five_of_fourteen_sources_block_publication():
    results = [_result(f"source_{i:02d}", i < 5) for i in range(14)]

    gate = realestate_watch.evaluate_source_gate(results)

    assert gate["ok"] is False
    assert gate["ok_count"] == 5
    assert len(gate["failed_sources"]) == 9
