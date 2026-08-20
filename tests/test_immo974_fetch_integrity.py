from types import SimpleNamespace

import pytest

from scripts import realestate_multi_sources_scraper as multi


def _article(sid: str = "d1") -> str:
    return (
        '<article><a href="/annonce/locations/appartement-'
        f'{sid}.html">Appartement</a></article>'
    )


def _use_legacy_fetch(monkeypatch, body: str) -> None:
    monkeypatch.setattr(multi, "_sf", None)
    monkeypatch.setattr(multi, "_legacy_fetch", lambda url, **_kwargs: (body, url))


def test_immo974_urllib_200_unusable_body_uses_exact_url_curl_cards(monkeypatch):
    route = multi.IMMO974_CITY_ROUTES["Saint-Denis"]
    curl_body = _article()
    calls = []
    _use_legacy_fetch(monkeypatch, "<html><body>generic shell</body></html>")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=curl_body.encode(), stderr=b"")

    monkeypatch.setattr(
        multi, "subprocess", SimpleNamespace(run=fake_run, TimeoutExpired=TimeoutError),
        raising=False,
    )

    text, final = multi.fetch(route)

    assert text == curl_body
    assert final == route
    assert calls[0][0][-1] == route
    assert multi.FETCH_LOG[-1]["ok"] is True
    assert multi.FETCH_LOG[-1]["mode"] == "curl_unusable_response_fallback"
    assert multi.FETCH_LOG[-1]["reason"] == "urllib_response_unusable"


def test_immo974_urllib_and_curl_unusable_bodies_fail_closed(monkeypatch):
    route = multi.IMMO974_CITY_ROUTES["Sainte-Marie"]
    _use_legacy_fetch(monkeypatch, "<html>generic urllib shell</html>")
    monkeypatch.setattr(
        multi,
        "subprocess",
        SimpleNamespace(
            run=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout=b"<html>generic curl shell</html>", stderr=b"",
            ),
            TimeoutExpired=TimeoutError,
        ),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="unusable"):
        multi.fetch(route)

    assert multi.FETCH_LOG[-1]["ok"] is False
    assert multi.FETCH_LOG[-1]["mode"] == "curl_unusable_response_fallback"
    assert multi.FETCH_LOG[-1]["reason"] == "curl_response_unusable"


def test_immo974_first_page_empty_without_zero_proof_is_partial(monkeypatch):
    monkeypatch.setattr(
        multi, "fetch", lambda url, **_kwargs: ("<html>generic empty shell</html>", url),
    )

    assert multi.scrape_immo974(delay=0) == []

    meta = multi.SOURCE_RUNTIME_META["immo974"]
    assert meta["full_snapshot_proof"] is False
    assert "unproven_empty_page" in meta["truncation_signals"]
    assert all(state["status"] == "partial" for state in meta["route_states"].values())


def test_immo974_explicit_zero_marker_allows_complete_empty_snapshot(monkeypatch):
    zero = '<meta name="total-results" content="0"><h1>0 annonce en location</h1>'
    monkeypatch.setattr(multi, "fetch", lambda url, **_kwargs: (zero, url))

    assert multi.scrape_immo974(delay=0) == []

    meta = multi.SOURCE_RUNTIME_META["immo974"]
    assert meta["full_snapshot_proof"] is True
    assert all(state["terminal"] == "explicit_zero" for state in meta["route_states"].values())
