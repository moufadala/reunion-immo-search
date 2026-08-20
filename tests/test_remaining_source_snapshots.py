from __future__ import annotations

from collections import defaultdict
import json
import sqlite3
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote_plus, urlsplit

import pytest

from scripts import realestate_multi_sources_scraper as multi


TARGET_CITIES = {"Saint-Denis", "Sainte-Marie"}


@pytest.fixture(autouse=True)
def _isolate_runtime(monkeypatch: pytest.MonkeyPatch):
    multi.SOURCE_RUNTIME_META.clear()
    multi.FETCH_LOG.clear()
    monkeypatch.setattr(multi.time, "sleep", lambda *_: None)
    monkeypatch.setattr(multi, "save_raw", lambda *_: None)


def _listing(source: str, sid: str, url: str, city: str, ptype: str = "flat"):
    return multi.Listing(
        source, sid, url, url, f"Appartement {sid}", city, None, ptype,
        3, 2, 70.0, 900, None, None, None, None, "description", None, "hash",
    )


def test_generic_upsert_reports_reappearance_and_manifest_balances_it():
    conn = sqlite3.connect(":memory:")
    multi.init_db(conn)
    row = _listing("ofim", "back", "https://example/back", "Saint-Denis")
    assert multi.upsert(conn, row) == "new"
    conn.execute(
        "UPDATE rental_listings SET is_active=0 WHERE source_site=? AND source_id=?",
        ("ofim", "back"),
    )
    status = multi.upsert(conn, row)
    assert status == "reappeared"
    manifest = multi.build_source_manifest(
        source="ofim", run_id="run-back", listings=[row],
        event_statuses=[status], source_status={"ok": True, "count": 1},
        fetch_log=[{"ok": True}], runtime_meta={
            "pages_attempted": 1, "pages_succeeded": 1,
            "raw_items": 1, "parsed_items": 1, "unique_ids": 1,
            "full_snapshot_proof": True, "truncation_signals": [],
            "rejected_items_by_reason": {},
        },
    )
    assert manifest["inserted"] == 0
    assert manifest["updated"] == 1
    assert manifest["unchanged"] == 0
    assert manifest["reappeared"] == 1


def test_generic_upsert_keeps_active_unchanged_listing_as_seen():
    conn = sqlite3.connect(":memory:")
    multi.init_db(conn)
    row = _listing("ofim", "same", "https://example/same", "Saint-Denis")
    assert multi.upsert(conn, row) == "new"
    assert multi.upsert(conn, row) == "seen"




def test_manifest_preserves_exact_stage_loss_accounting():
    row = _listing('ofim', 'kept', 'https://example/kept', 'Saint-Denis')
    manifest = multi.build_source_manifest(
        source='ofim', run_id='stages', listings=[row], event_statuses=['seen'],
        source_status={'ok': True, 'count': 1}, fetch_log=[{'ok': True}],
        runtime_meta={
            'pages_attempted': 1, 'pages_succeeded': 1,
            'raw_items': 4, 'parsed_items': 3, 'unique_ids': 2,
            'unparsed_items_by_reason': {'missing_id': 1},
            'pre_unique_rejections_by_reason': {'duplicate_raw': 1},
            'rejected_items_by_reason': {'out_of_scope': 1},
            'full_snapshot_proof': True, 'truncation_signals': [],
        },
    )
    assert manifest['status'] == 'partial'
    assert 'unparsed_items_present:1' in manifest['truncation_signals']
    assert manifest['fetched_items'] == 4
    assert manifest['parsed_items'] == 3
    assert manifest['unique_ids'] == 2
    assert manifest['normalized_items'] == 1
    assert manifest['unparsed_items_by_reason'] == {'missing_id': 1}
    assert manifest['pre_unique_rejections_by_reason'] == {'duplicate_raw': 1}
    assert manifest['rejected_items_by_reason'] == {'out_of_scope': 1}


def test_manifest_downgrades_inexact_stage_loss_accounting():
    row = _listing('ofim', 'kept', 'https://example/kept', 'Saint-Denis')
    manifest = multi.build_source_manifest(
        source='ofim', run_id='bad-stages', listings=[row], event_statuses=['seen'],
        source_status={'ok': True, 'count': 1}, fetch_log=[{'ok': True}],
        runtime_meta={
            'pages_attempted': 1, 'pages_succeeded': 1,
            'raw_items': 4, 'parsed_items': 3, 'unique_ids': 2,
            'unparsed_items_by_reason': {},
            'pre_unique_rejections_by_reason': {},
            'rejected_items_by_reason': {'out_of_scope': 1},
            'full_snapshot_proof': True, 'truncation_signals': [],
        },
    )
    assert manifest['status'] == 'partial'
    assert 'stage_accounting_mismatch' in manifest['truncation_signals']
def _assert_complete_accounting(
    source: str,
    *,
    raw: int,
    unique: int,
    normalized: int,
    rejected: dict[str, int] | None = None,
):
    assert source in multi.SOURCE_RUNTIME_META
    meta = multi.SOURCE_RUNTIME_META[source]
    assert meta["full_snapshot_proof"] is True
    assert meta["truncation_signals"] == []
    assert meta["raw_items"] == raw
    assert meta["unique_ids"] == unique
    assert raw >= meta["parsed_items"] >= unique >= normalized
    assert sum(meta.get("unparsed_items_by_reason", {}).values()) == raw - meta["parsed_items"]
    assert sum(meta.get("pre_unique_rejections_by_reason", {}).values()) == meta["parsed_items"] - unique
    assert meta["rejected_items_by_reason"] == (rejected or {})
    assert sum(meta["rejected_items_by_reason"].values()) == unique - normalized


def _immo974_article(sid: str, city: str, kind: str = "appartement") -> str:
    return f"""
    <article>
      <a class="img-list" href="/annonce/locations/{kind}-{sid}.html"><img src="/{sid}.jpg"></a>
      <h2 class="ville-type"><a title="{kind.title()} T3 70 m2">{kind}</a></h2>
      <h2 class="localisation"><i></i>{city}</h2>
      <div class="price-result"><b>900 €</b></div>
      <div class="date_publication">18/08/2026</div>
      <p class="description">Location résidentielle {city}</p>
    </article>
    """


def test_immo974_uses_only_target_city_catalogues_and_accounts_for_scope_leaks(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_fetch(url: str, method: str = "GET", data=None):
        calls.append((url, method))
        decoded = unquote_plus(url).lower()
        if "saint denis" in decoded:
            return (
                '<h1>2 annonces en location</h1>'
                + _immo974_article("d1", "Sainte Clotilde")
                + _immo974_article("x1", "Saint-André"),
                url,
            )
        if "sainte marie" in decoded:
            return '<h1>1 annonce en location</h1>' + _immo974_article("m1", "Sainte Marie"), url
        return '<h1>1 annonce en location</h1>' + _immo974_article("island", "Saint-Paul"), url

    monkeypatch.setattr(multi, "fetch", fake_fetch)

    rows = multi.scrape_immo974(max_pages=50, delay=0)

    assert {row.source_id for row in rows} == {"d1", "m1"}
    assert {row.city for row in rows} == TARGET_CITIES
    assert all(method == "GET" for _, method in calls)
    assert all(
        "saint denis" in unquote_plus(url).lower()
        or "sainte marie" in unquote_plus(url).lower()
        for url, _ in calls
    )
    _assert_complete_accounting(
        "immo974", raw=3, unique=3, normalized=2,
        rejected={"out_of_scope": 1},
    )


def _locamoi_page(sid: str, city: str, kind: str, total: int = 1) -> str:
    item = {
        "position": 1,
        "item": {
            "url": f"https://locamoi.fr/listings/location-{kind}-{sid}",
            "name": f"{kind.title()} T3 {city}",
            "image": f"https://img.example/{sid}.jpg",
            "offers": {
                "url": f"https://locamoi.fr/listings/location-{kind}-{sid}",
                "price": 900,
                "itemOffered": {
                    "address": {"addressLocality": city},
                    "floorSize": {"value": 70},
                    "numberOfBedrooms": {"value": 2},
                },
            },
        },
    }
    payload = {"@type": "ItemList", "numberOfItems": total, "mainEntity": {"itemListElement": [item]}}
    return (
        f'<meta name="total-results" content="{total}">'
        f'<script type="application/ld+json">{json.dumps(payload)}</script>'
    )


def test_locamoi_exhausts_apartment_and_house_routes_for_both_target_cities(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url: str, method: str = "GET", data=None):
        calls.append(url)
        low = url.lower()
        if "la-reunion-saint-denis" in low:
            kind = "maison" if "/maison/" in low else "appartement"
            return _locamoi_page(f"d-{kind}", "Saint-Denis", kind), url
        if "la-reunion-sainte-marie" in low:
            kind = "maison" if "/maison/" in low else "appartement"
            return _locamoi_page(f"m-{kind}", "Sainte-Marie", kind), url
        return _locamoi_page("island", "Saint-Paul", "appartement"), url

    monkeypatch.setattr(multi, "fetch", fake_fetch)

    rows = multi.scrape_locamoi(max_pages=50, delay=0)

    assert {row.city for row in rows} == TARGET_CITIES
    assert len(rows) == 4
    assert {row.property_type for row in rows} == {"flat", "house"}
    assert {row.rooms for row in rows} == {3}
    assert {row.bedrooms for row in rows} == {2}
    assert len(calls) == 4
    assert all("la-reunion-saint" in url or "la-reunion-sainte" in url for url in calls)
    _assert_complete_accounting("locamoi", raw=4, unique=4, normalized=4)


def _immo97_links(*paths: str) -> str:
    return "".join(f'<a href="{path}">annonce</a>' for path in paths)


def test_97immo_limits_routes_to_two_cities_and_counts_non_residential_rejections(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url: str, method: str = "GET", data=None):
        calls.append(url)
        low = unquote_plus(url).lower()
        if "page=" in low:
            return "", url
        if "[]=195" in low:
            return _immo97_links(
                "/immobilier-annonce/location/appartement/saint-denis/la-reunion/d/1",
                "/immobilier-annonce/location/local-commercial/saint-denis/la-reunion/c/1",
            ), url
        if "[]=82" in low:
            return _immo97_links(
                "/immobilier-annonce/location/maison/sainte-marie/la-reunion/m/1",
            ), url
        return _immo97_links(
            "/immobilier-annonce/location/appartement/saint-andre/la-reunion/x/1",
        ), url

    def fake_detail(source: str, url: str, ptype=None, sid=None):
        city = "Sainte-Marie" if "sainte-marie" in url else (
            "Saint-Denis" if "saint-denis" in url else "Saint-André"
        )
        return _listing(source, sid or url, url, city, ptype or "flat")

    monkeypatch.setattr(multi, "fetch", fake_fetch)
    monkeypatch.setattr(multi, "detail_listing", fake_detail)

    rows = multi.scrape_97immo(max_items=5000, max_pages=50, delay=0)

    assert {row.city for row in rows} == TARGET_CITIES
    assert len(rows) == 2
    assert all("[]=195" in unquote_plus(url) or "[]=82" in unquote_plus(url) for url in calls)
    _assert_complete_accounting(
        "97immo", raw=3, unique=3, normalized=2,
        rejected={"non_residential": 1},
    )


def test_ofim_exhausts_both_residential_categories_and_filters_after_counting(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url: str, method: str = "GET", data=None):
        calls.append(url)
        if "liste-location-appartements" in url:
            return (
                "rc1=11"
                + _immo97_links(
                    "https://www.ofim.fr/101/Location-Appartement-SAINTE-CLOTILDE-demo.html",
                    "https://www.ofim.fr/102/Location-Appartement-SAINT-PAUL-demo.html",
                ),
                url,
            )
        if "liste-location-villas" in url:
            return (
                "rc1=22"
                + _immo97_links(
                    "https://www.ofim.fr/201/Location-Maison-Villa-SAINTE-MARIE-demo.html",
                ),
                url,
            )
        if "recherche.html" in url:
            return "", url
        raise AssertionError(url)

    def fake_detail(source: str, url: str, ptype=None, sid=None):
        if "SAINT-PAUL" in url:
            city = "Saint-Paul"
        elif "SAINTE-MARIE" in url:
            city = "Sainte-Marie"
        else:
            city = "Sainte Clotilde"
        return _listing(source, sid or url, url, city, ptype or "flat")

    monkeypatch.setattr(multi, "fetch", fake_fetch)
    monkeypatch.setattr(multi, "detail_listing", fake_detail)

    rows = multi.scrape_ofim(max_items=5000, max_pages=50, delay=0)

    assert {row.source_id for row in rows} == {"101", "201"}
    assert {row.city for row in rows} == TARGET_CITIES
    assert sum("recherche.html" in url for url in calls) == 2
    _assert_complete_accounting(
        "ofim", raw=3, unique=3, normalized=2,
        rejected={"out_of_scope": 1},
    )


def _alter_html(*slugs: str, next_page: bool = False) -> str:
    payload = "".join(
        f'"https:\\/\\/alter-immobilier.re\\/post_type_annonces\\/{slug}"'
        for slug in slugs
    )
    return payload + ('<a rel="next" href="?paged=2">Suivant</a>' if next_page else "")


def test_alter_single_page_catalogue_is_complete_only_after_scope_accounting(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url: str, method: str = "GET", data=None):
        calls.append(url)
        return _alter_html(
            "a-louer-appartement-saint-denis-d1",
            "a-louer-maison-sainte-marie-m1",
            "a-louer-appartement-saint-paul-x1",
        ), url

    def fake_detail(source: str, url: str, ptype=None, sid=None):
        city = "Sainte-Marie" if "sainte-marie" in url else (
            "Saint-Denis" if "saint-denis" in url else "Saint-Paul"
        )
        return _listing(source, sid or url, url, city, ptype or "flat")

    monkeypatch.setattr(multi, "fetch", fake_fetch)
    monkeypatch.setattr(multi, "detail_listing", fake_detail)

    rows = multi.scrape_alter(max_items=5000)

    assert {row.city for row in rows} == TARGET_CITIES
    assert len(calls) == 1
    _assert_complete_accounting(
        "alter", raw=3, unique=3, normalized=2,
        rejected={"out_of_scope": 1},
    )
    assert multi.SOURCE_RUNTIME_META["alter"]["route_states"]["catalogue"]["terminal"] == "no_next"


def test_alter_does_not_claim_complete_when_the_catalogue_advertises_pagination(monkeypatch):
    monkeypatch.setattr(
        multi,
        "fetch",
        lambda url, method="GET", data=None: (
            _alter_html("a-louer-appartement-saint-denis-d1", next_page=True), url
        ),
    )
    monkeypatch.setattr(
        multi,
        "detail_listing",
        lambda source, url, ptype=None, sid=None: _listing(source, sid or url, url, "Saint-Denis"),
    )

    multi.scrape_alter(max_items=5000)

    meta = multi.SOURCE_RUNTIME_META["alter"]
    assert meta["full_snapshot_proof"] is False
    assert meta["truncation_signals"]


def _adrezio_page(sid: str, city: str) -> str:
    return f"""
    <meta name="total-results" content="1">
    <a href="/annonces/{sid}">
      <img src="/{sid}.jpg" alt="Photo 1 - Appartement T3 à louer {city}">
      <span>{city}</span><span>900 € / mois</span><span>70 m²</span><span>3 pièces</span>
    </a>
    """


def test_adrezio_exhausts_exactly_four_target_city_type_routes(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url: str, method: str = "GET", data=None):
        calls.append(url)
        low = url.lower()
        kind = "m" if "/maison/" in low else "a"
        if "/saint-denis" in low:
            return _adrezio_page(f"d{kind}", "Saint-Denis"), url
        if "/sainte-marie" in low:
            return _adrezio_page(f"m{kind}", "Sainte-Marie"), url
        if "/saint-andre" in low:
            return _adrezio_page(f"x{kind}", "Saint-André"), url
        return _adrezio_page(f"s{kind}", "Sainte-Suzanne"), url

    monkeypatch.setattr(multi, "fetch", fake_fetch)

    rows = multi.scrape_adrezio(max_items=5000, max_pages=50, delay=0)

    assert {row.city for row in rows} == TARGET_CITIES
    assert len(rows) == 4
    assert len(calls) == 4
    assert not any("saint-andre" in url or "sainte-suzanne" in url for url in calls)
    _assert_complete_accounting("adrezio", raw=4, unique=4, normalized=4)


def _domimmo_item(
    sid: str,
    city: str,
    *,
    transaction: int = 2,
    location: str = "REU",
    kind: int = 1,
):
    title = "Appartement T3 à louer" if kind == 1 else "Maison T3 à louer"
    if transaction != 2:
        title = title.replace("à louer", "à vendre")
    return {
        "id": sid,
        "reference": sid,
        "title": title,
        "description": f"Bien résidentiel situé à {city}",
        "id_di_ad_cat": transaction,
        "id_di_ad_type": kind,
        "location": location,
        "city": city,
        "surface_habitable": 70,
        "pieces": 3,
        "chambres": 2,
        "price": 900,
        "photos": [f"https://img.example/{sid}.jpg"],
    }


def test_domimmo_non_saturated_api_snapshot_accounts_for_every_unique_item(monkeypatch):
    payload = [
        _domimmo_item("d1", "Saint-Denis"),
        _domimmo_item("m1", "Sainte-Marie", kind=2),
        _domimmo_item("x1", "Saint-Paul"),
        _domimmo_item("sale1", "Saint-Denis", transaction=1),
    ]
    monkeypatch.setattr(
        multi,
        "fetch",
        lambda url, method="GET", data=None: (json.dumps(payload), url),
    )

    rows = multi.scrape_domimmo(max_items=5000)

    assert {row.source_id for row in rows} == {"d1", "m1"}
    assert {row.city for row in rows} == TARGET_CITIES
    _assert_complete_accounting(
        "domimmo", raw=4, unique=4, normalized=2,
        rejected={"out_of_scope": 1, "not_rental": 1},
    )
    assert multi.SOURCE_RUNTIME_META["domimmo"]["snapshot_proof"] == "api_response_below_limit"


def test_domimmo_full_api_page_is_partial_not_a_false_complete_snapshot(monkeypatch):
    requested_limits: list[int] = []

    def fake_fetch(url: str, method: str = "GET", data=None):
        limit = int(parse_qs(urlsplit(url).query)["limit"][0])
        requested_limits.append(limit)
        payload = [_domimmo_item(f"d{i}", "Saint-Denis") for i in range(limit)]
        return json.dumps(payload), url

    monkeypatch.setattr(multi, "fetch", fake_fetch)

    multi.scrape_domimmo(max_items=5000)

    meta = multi.SOURCE_RUNTIME_META["domimmo"]
    assert requested_limits
    assert meta["full_snapshot_proof"] is False
    assert any("limit" in signal or "cap" in signal for signal in meta["truncation_signals"])


@pytest.mark.parametrize(
    ("source", "runner"),
    [
        ("immo974", lambda: multi.scrape_immo974(max_pages=50, delay=0)),
        ("locamoi", lambda: multi.scrape_locamoi(max_pages=50, delay=0)),
        ("97immo", lambda: multi.scrape_97immo(max_items=5000, max_pages=50, delay=0)),
        ("ofim", lambda: multi.scrape_ofim(max_items=5000, max_pages=50, delay=0)),
        ("alter", lambda: multi.scrape_alter(max_items=5000)),
        ("adrezio", lambda: multi.scrape_adrezio(max_items=5000, max_pages=50, delay=0)),
        ("domimmo", lambda: multi.scrape_domimmo(max_items=5000)),
    ],
)
def test_fetch_failure_never_produces_authoritative_snapshot(monkeypatch, source, runner):
    monkeypatch.setattr(multi, "fetch", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("boom")))

    try:
        runner()
    except TimeoutError:
        pass

    assert source in multi.SOURCE_RUNTIME_META
    meta = multi.SOURCE_RUNTIME_META[source]
    assert meta["full_snapshot_proof"] is False
    assert "fetch_failure" in meta["truncation_signals"]
