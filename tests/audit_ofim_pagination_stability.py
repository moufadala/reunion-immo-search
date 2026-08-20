#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "realestate_multi_sources_scraper.py"


def load_module():
    spec = importlib.util.spec_from_file_location("realestate_multi_sources_scraper", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def links_html(ids):
    return "".join(
        f'<a href="https://www.ofim.fr/{i}/Location-appartement-{i}.html">annonce {i}</a>'
        for i in ids
    )


def target_listing(mod, source, url, ptype, sid):
    return mod.Listing(
        source, sid, url, url, None, 'Saint-Denis', None, ptype,
        None, None, None, None, None, None, None, None, None, None, 'hash',
    )

def test_ofim_does_not_stop_on_full_overlap_page_before_later_new_items():
    mod = load_module()
    mod.time.sleep = lambda *args, **kwargs: None
    mod.detail_listing = lambda source, url, ptype=None, sid=None: target_listing(mod, source, url, ptype, sid)

    def fake_fetch(url, method="GET", data=None):
        if "liste-location-appartements" in url:
            # The seed/static page and first paginated window can overlap when
            # OFIM reorders its list. The scraper must not infer catalogue end
            # from this overlap alone.
            return links_html(range(1, 11)) + "rc1=99", url
        if "liste-location-villas" in url:
            return "", url
        if "start=10" in url:
            return links_html(range(1, 11)), url
        if "start=20" in url:
            return links_html(range(11, 21)), url
        return "", url

    mod.fetch = fake_fetch
    got = {row.source_id for row in mod.scrape_ofim(max_items=90, max_pages=6, delay=0)}

    expected = {str(i) for i in range(1, 21)}
    assert expected <= got, f"OFIM queue lost after overlap page: {sorted(expected - got)}"


def test_ofim_stops_on_empty_page():
    mod = load_module()
    mod.time.sleep = lambda *args, **kwargs: None
    mod.detail_listing = lambda source, url, ptype=None, sid=None: target_listing(mod, source, url, ptype, sid)
    calls = []

    def fake_fetch(url, method="GET", data=None):
        calls.append(url)
        if "liste-location-appartements" in url:
            return links_html(range(1, 4)) + "rc1=99", url
        if "liste-location-villas" in url:
            return "", url
        if "start=10" in url:
            return "", url
        raise AssertionError(f"scraper continued after empty OFIM page: {url}")

    mod.fetch = fake_fetch
    got = {row.source_id for row in mod.scrape_ofim(max_items=90, max_pages=6, delay=0)}
    assert got == {"1", "2", "3"}
    assert any("start=10" in u for u in calls)


if __name__ == "__main__":
    test_ofim_does_not_stop_on_full_overlap_page_before_later_new_items()
    test_ofim_stops_on_empty_page()
    print("OFIM_PAGINATION_STABILITY_AUDIT PASS")
