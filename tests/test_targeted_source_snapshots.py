from __future__ import annotations

from collections import defaultdict
import re

from types import SimpleNamespace
import pytest

from scripts import realestate_multi_sources_scraper as multi


def _listing_page(*ids: str, next_page: bool = False, total: int | None = None) -> str:
    total_html = f'<meta name="total-results" content="{total}">' if total is not None else ""
    cards = "".join(
        f'<article data-public-id="{sid}"><a data-js-url="/annonces/{sid}">'
        f'Appartement {sid} 70 m2 900 € CC</a></article>'
        for sid in ids
    )
    next_html = '<a rel="next" href="/p/2">Suivant</a>' if next_page else ""
    return total_html + cards + next_html


def _zimo_page(
    *ids: str,
    city: str = "Saint-Denis",
    next_page: bool = False,
    total: int | None = None,
) -> str:
    total_html = f'<meta name="total-results" content="{total}">' if total is not None else ""
    cards = "".join(
        f'<article><a href="/annonce/{sid}" title="Appartement {sid}">Annonce</a>'
        f'<span class="badge ink base">900 €</span>'
        f'<div class="font-medium">Location Appartement {city} (974) 70 m2</div></article>'
        for sid in ids
    )
    next_html = '<a rel="next" href="?page=2">Suivant</a>' if next_page else ""
    return total_html + cards + next_html


def _fnaim_page(*ids: str, next_page: bool = False, total: int | None = None) -> str:
    total_html = f'<meta name="total-results" content="{total}">' if total is not None else ""
    links = "".join(
        f'<a href="/location/appartement-st-denis-id-location-demo-{sid}/">Bien {sid}</a>'
        for sid in ids
    )
    next_html = '<a rel="next" href="/2">Suivant</a>' if next_page else ""
    return total_html + links + next_html


def _citya_page(*cards: str, next_page: bool = False, total: int | None = None) -> str:
    total_html = f'<meta name="total-results" content="{total}">' if total is not None else ""
    next_html = '<a rel="next" href="?page=2">Suivant</a>' if next_page else ""
    return total_html + "".join(cards) + next_html


def _citya_card(item_id: str, city: str) -> str:
    slug = city.lower().replace(" ", "-").replace("é", "e")
    return (
        f'<article class="property-card" data-itemId="{item_id}">'
        f'<a href="/annonces/location/appartement/{slug}-97400/{item_id}">'
        f'Appartement à louer à {city}</a></article>'
    )


def _install_fetch(monkeypatch: pytest.MonkeyPatch, pages: dict[str, list[str]]):
    calls: dict[str, int] = defaultdict(int)

    def fake_fetch(url: str, method: str = "GET", data=None):
        base = next(
            (
                candidate for candidate in pages
                if url == candidate or url.startswith(candidate + "?page=")
                or url.startswith(candidate + "/p/")
                or re.fullmatch(re.escape(candidate) + r"/[0-9]+", url)
            ), url,
        )
        index = calls[base]
        calls[base] += 1
        sequence = pages[base]
        if index >= len(sequence):
            raise AssertionError(f"unexpected fetch {url}")
        return sequence[index], url

    monkeypatch.setattr(multi, "fetch", fake_fetch)
    monkeypatch.setattr(multi.time, "sleep", lambda *_: None)
    monkeypatch.setattr(multi, "save_raw", lambda *_: None)
    monkeypatch.setattr(
        multi,
        "detail_listing",
        lambda source, url, ptype=None, sid=None: multi.Listing(
            source, sid or url.rsplit("/", 1)[-1], url, url, url, multi.city_from_url(url),
            None, ptype, 3, 2, 70.0, 900, None, None, None, None, "detail", None, "hash",
        ),
    )
    return calls


def test_manifest_allows_proven_empty_snapshot_to_be_complete():
    manifest = multi.build_source_manifest(
        source="citya", run_id="empty-ok", listings=[], event_statuses=[],
        source_status={"ok": False, "count": 0}, fetch_log=[],
        runtime_meta={
            "pages_attempted": 4, "pages_succeeded": 4,
            "raw_items": 0, "parsed_items": 0, "unique_ids": 0,
            "full_snapshot_proof": True,
            "snapshot_proof": "all_target_routes_exhausted",
            "truncation_signals": [],
        },
    )

    assert manifest["status"] == "complete"
    assert manifest["normalized_items"] == 0
    assert manifest["error"] is None


def test_manifest_keeps_failed_empty_fetch_non_authoritative():
    manifest = multi.build_source_manifest(
        source="citya", run_id="empty-failed", listings=[], event_statuses=[],
        source_status={"ok": False, "count": 0, "error": "timeout"},
        fetch_log=[{"ok": False}],
        runtime_meta={
            "pages_attempted": 1, "pages_succeeded": 0,
            "raw_items": 0, "parsed_items": 0, "unique_ids": 0,
            "full_snapshot_proof": False,
            "truncation_signals": ["fetch_failure"],
        },
    )

    assert manifest["status"] == "failed"
    assert manifest["error"] == "timeout"


def test_manifest_copies_exact_rejection_reasons():
    manifest = multi.build_source_manifest(
        source="zimo", run_id="reject-ok",
        listings=[SimpleNamespace(source_id="kept")], event_statuses=["seen"],
        source_status={"ok": True, "count": 1}, fetch_log=[],
        runtime_meta={
            "pages_attempted": 2, "pages_succeeded": 2,
            "raw_items": 3, "parsed_items": 3, "unique_ids": 3,
            "full_snapshot_proof": True,
            "rejected_items_by_reason": {"out_of_scope": 1, "commercial": 1},
            "pre_unique_rejections_by_reason": {"duplicate_raw": 2, "missing_id": 1},
        },
    )

    assert manifest["rejected_items"] == 2
    assert manifest["rejected_items_by_reason"] == {"out_of_scope": 1, "commercial": 1}
    assert manifest["pre_unique_rejections_by_reason"] == {"duplicate_raw": 2, "missing_id": 1}
    assert "rejection_accounting_mismatch" not in manifest["truncation_signals"]


def test_manifest_blocks_unbalanced_rejection_reasons():
    manifest = multi.build_source_manifest(
        source="zimo", run_id="reject-bad",
        listings=[SimpleNamespace(source_id="kept")], event_statuses=["seen"],
        source_status={"ok": True, "count": 1}, fetch_log=[],
        runtime_meta={
            "pages_attempted": 2, "pages_succeeded": 2,
            "raw_items": 3, "parsed_items": 3, "unique_ids": 3,
            "full_snapshot_proof": True,
            "rejected_items_by_reason": {"out_of_scope": 1},
        },
    )

    assert manifest["status"] == "partial"
    assert "rejection_accounting_mismatch" in manifest["truncation_signals"]


def test_next_page_accepts_fnaim_link_rel_markup():
    source = '<link rel="next" href="https://www.fnaim.re/38244-st-denis/locations/2"/>'

    assert multi._has_next_page(source, 2) is True


def test_citya_strict_card_parser_handles_real_nested_dom():
    source = """
    <div class="property-card" data-itemId="GES79000001-34">
      <div class="relative"><div class="swiper"><img src="photo.webp"></div></div>
      <div class="content">
        <a href="https://www.citya.com/annonces/location/appartement/saint-denis-97411/GES79000001-34">
          Saint-Denis (97400) Appartement 2 pièces 23m²
        </a>
      </div>
    </div>
    """

    cards = multi._citya_strict_cards(source)

    assert [(item_id, ptype) for item_id, _, ptype, _ in cards] == [
        ("GES79000001-34", "appartement")
    ]


def test_target_routes_cover_only_saint_denis_and_sainte_marie():
    assert set(multi.ZIMO_CITY_ROUTES) == {"Saint-Denis", "Sainte-Marie"}
    assert set(multi.FNAIM_CITY_ROUTES) == {"Saint-Denis", "Sainte-Marie"}
    assert set(multi.SUPERIMMO_CITY_ROUTES) == {"Saint-Denis", "Sainte-Marie"}
    assert set(multi.CITYA_COMMUNES) == {"Saint-Denis", "Sainte-Marie"}
    assert multi.ZIMO_CITY_ROUTES["Saint-Denis"].endswith("/saint-denis-97400")
    assert multi.FNAIM_CITY_ROUTES["Saint-Denis"].endswith("/38244-st-denis/locations")
    assert multi.FNAIM_CITY_ROUTES["Sainte-Marie"].endswith("/38245-ste-marie/locations/appartements")
    assert multi.SUPERIMMO_CITY_ROUTES["Sainte-Marie"].endswith("/sainte-marie-97438")
    assert multi.SUPERIMMO_CITY_ROUTES["Saint-Denis"].endswith("/saint-denis-974")


def test_cloudflare_challenge_is_not_accepted_as_empty_snapshot(monkeypatch):
    challenge = """
    <html><head><title>Attention Required! | Cloudflare</title></head>
    <body>Sorry, you have been blocked. Enable cookies.</body></html>
    """

    def fake_fetch(url: str, method: str = "GET", data=None):
        return challenge, url

    monkeypatch.setattr(multi, "fetch", fake_fetch)
    monkeypatch.setattr(multi.time, "sleep", lambda *_: None)
    monkeypatch.setattr(multi, "save_raw", lambda *_: None)

    listings = multi.scrape_zimo(max_pages=50, delay=0)

    assert listings == []
    meta = multi.SOURCE_RUNTIME_META["zimo"]
    assert meta["full_snapshot_proof"] is False
    assert "blocked_or_challenge_page" in meta["truncation_signals"]


def test_zimo_full_snapshot_requires_every_city_to_reach_empty_terminal(monkeypatch):
    pages = {
        "https://www.zimo.fr/annonces/immobilier/location/saint-denis-97400": [
            _zimo_page("d1", next_page=True), _zimo_page("d2", next_page=True), ""
        ],
        "https://www.zimo.fr/annonces/immobilier/location/sainte-marie-97438": [
            _zimo_page("m1", city="Sainte-Marie", next_page=True), ""
        ],
    }
    calls = _install_fetch(monkeypatch, pages)

    listings = multi.scrape_zimo(max_pages=50, delay=0)

    assert {item.source_id for item in listings} == {"d1", "d2", "m1"}
    assert list(calls.values()) == [3, 2]
    assert multi.SOURCE_RUNTIME_META["zimo"]["full_snapshot_proof"] is True
    assert multi.SOURCE_RUNTIME_META["zimo"]["truncation_signals"] == []


def test_zimo_item_safety_cap_limits_output_and_marks_partial(monkeypatch):
    pages = {
        multi.ZIMO_CITY_ROUTES["Saint-Denis"]: [_zimo_page("d1", "d2", next_page=True)],
        multi.ZIMO_CITY_ROUTES["Sainte-Marie"]: [_zimo_page("m1", city="Sainte-Marie")],
    }
    _install_fetch(monkeypatch, pages)

    listings = multi.scrape_zimo(max_pages=50, max_items=1, delay=0)

    assert [item.source_id for item in listings] == ["d1"]
    meta = multi.SOURCE_RUNTIME_META["zimo"]
    assert meta["full_snapshot_proof"] is False
    assert "safety_item_cap_reached:1" in meta["truncation_signals"]
    assert meta["rejected_items_by_reason"]["safety_item_cap"] == 1


def test_superimmo_full_snapshot_can_be_proved_by_reported_total(monkeypatch):
    pages = {
        multi.SUPERIMMO_CITY_ROUTES["Saint-Denis"]: [_listing_page("d1", "d2", total=2)],
        multi.SUPERIMMO_CITY_ROUTES["Sainte-Marie"]: [_listing_page("m1", total=1)],
    }
    calls = _install_fetch(monkeypatch, pages)

    listings = multi.scrape_superimmo(max_pages=50, delay=0)

    assert {item.source_id for item in listings} == {"d1", "d2", "m1"}
    assert list(calls.values()) == [1, 1]
    assert multi.SOURCE_RUNTIME_META["superimmo"]["snapshot_proof"] == "all_target_routes_exhausted"


def test_fnaim_safety_cap_is_partial_even_when_pages_succeed(monkeypatch):
    pages = {
        multi.FNAIM_CITY_ROUTES["Saint-Denis"]: [_fnaim_page("1", next_page=True), _fnaim_page("2", next_page=True)],
        multi.FNAIM_CITY_ROUTES["Sainte-Marie"]: [_fnaim_page("3", next_page=True), _fnaim_page("4", next_page=True)],
    }
    _install_fetch(monkeypatch, pages)

    multi.scrape_fnaim(max_pages=2, delay=0)

    meta = multi.SOURCE_RUNTIME_META["fnaim"]
    assert meta["full_snapshot_proof"] is False
    assert "safety_page_cap_reached:2" in meta["truncation_signals"]


def test_fetch_failure_makes_source_partial_without_hiding_previous_pages(monkeypatch):
    first = multi.ZIMO_CITY_ROUTES["Saint-Denis"]
    second = multi.ZIMO_CITY_ROUTES["Sainte-Marie"]
    attempts = defaultdict(int)

    def fake_fetch(url: str, method: str = "GET", data=None):
        base = url.split("?page=", 1)[0]
        attempts[base] += 1
        if base == first and attempts[base] == 1:
            return _zimo_page("d1", next_page=True), url
        if base == first:
            raise TimeoutError("simulated timeout")
        return "", url

    monkeypatch.setattr(multi, "fetch", fake_fetch)
    monkeypatch.setattr(multi.time, "sleep", lambda *_: None)
    monkeypatch.setattr(multi, "save_raw", lambda *_: None)

    listings = multi.scrape_zimo(max_pages=50, delay=0)

    assert [item.source_id for item in listings] == ["d1"]
    meta = multi.SOURCE_RUNTIME_META["zimo"]
    assert meta["full_snapshot_proof"] is False
    assert "fetch_failure" in meta["truncation_signals"]
    assert meta["pages_attempted"] == 3
    assert meta["pages_succeeded"] == 2


def test_citya_uses_strict_cards_and_rejects_goldens_outside_target_scope(monkeypatch):
    in_scope = _citya_card("GES11111111-542", "Saint-Denis")
    saint_andre_1 = _citya_card("GES27390302-542", "Saint-André")
    saint_andre_2 = _citya_card("GES56851227-542", "Saint-André")
    related_carousel = '<div class="related-carousel" data-itemId="GES99999999-542"></div>'
    pages = {}
    for commune, slug in multi.CITYA_COMMUNES.items():
        for ptype in ("appartement", "maison"):
            route = f"https://www.citya.com/annonces/location/{ptype}/{slug}"
            pages[route] = [
                _citya_page(in_scope, saint_andre_1, saint_andre_2, related_carousel),
                "",
            ]
    _install_fetch(monkeypatch, pages)

    listings = multi.scrape_citya(max_pages=50, delay=0)

    assert {item.source_id for item in listings} == {"GES11111111-542"}
    assert all(item.city in {"Saint-Denis", "Sainte-Marie"} for item in listings)
    meta = multi.SOURCE_RUNTIME_META["citya"]
    assert meta["full_snapshot_proof"] is True
    assert meta["rejected_out_of_scope"] >= 2
    assert meta["rejected_non_card"] >= 1
