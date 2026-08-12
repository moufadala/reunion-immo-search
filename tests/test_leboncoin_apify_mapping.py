#!/usr/bin/env python3
"""Unit tests for Leboncoin Apify dataset -> Listing mapping.

Pure mapping tests: no network and no DB. They pin the contract required by the
brief: residential-only result filter, native stable source_id, distinct
source_site='leboncoin', and tolerance for native + flattened Apify item shapes.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "realestate_multi_sources_scraper.py"
spec = importlib.util.spec_from_file_location("rms_scraper", MOD_PATH)
assert spec is not None and spec.loader is not None
rms = importlib.util.module_from_spec(spec)
sys.modules["rms_scraper"] = rms
spec.loader.exec_module(rms)


def native_apartment() -> dict:
    return {
        "list_id": 2712345678,
        "subject": "Appartement T3 lumineux",
        "body": "Bel appartement proche centre.",
        "url": "https://www.leboncoin.fr/ad/locations/2712345678",
        "price": [850],
        "firstPublicationDate": "2026-07-28T10:00:00Z",
        "location": {"city": "Saint-Denis", "zipcode": "97400"},
        "images": {"urls_large": ["https://img.leboncoin.fr/a.jpg"], "thumb_url": "https://img/t.jpg"},
        "owner": {"name": "Agence Nord", "type": "pro"},
        "attributes": [
            {"key": "real_estate_type", "value": "2", "value_label": "Appartement"},
            {"key": "rooms", "value": "3", "value_label": "3"},
            {"key": "square", "value": "65", "value_label": "65 m²"},
        ],
    }


def real_piotrv1001_apartment() -> dict:
    """Reduced real item shape from piotrv1001/leboncoin-listings-scraper.

    This is the shape that f72a436 missed: camelCase listId, seller, attributes
    as a flat dict, and image dicts without a generic `url` key.
    """
    return {
        "listId": 3239722405,
        "title": "Studio St Denis Bellepierre",
        "description": "Tres beau studio meuble de 32m2 avec parking...",
        "url": "https://www.leboncoin.fr/ad/locations/3239722405",
        "price": 680,
        "firstPublicationDate": "2026-07-26 16:37:18",
        "location": {"city": "Saint-Denis", "zipcode": "97400", "region": "Reunion"},
        "seller": {"storeId": "1516927", "name": "arobase", "type": "private"},
        "images": [{
            "thumbnailUrl": "https://img.leboncoin.fr/thumb.jpg?rule=ad-thumb",
            "imageUrl": "https://img.leboncoin.fr/image.jpg?rule=ad-image",
            "largeUrl": "https://img.leboncoin.fr/large.jpg?rule=ad-large",
        }],
        "attributes": {
            "real_estate_type": "Appartement", "square": "32 m2", "rooms": "1",
            "bedrooms": "1 ch.", "floor_number": "1", "nb_floors_building": "5",
            "floor_display": "Etage 1/5", "elevator": "Oui", "nb_bathrooms": "1",
            "furnished": "Meuble", "monthly_charges": "50 EUR", "charges_included": "Oui",
        },
    }


def test_maps_real_piotrv1001_item_shape() -> None:
    listing = rms._map_leboncoin_item(real_piotrv1001_apartment())
    assert listing is not None
    assert listing.source_site == "leboncoin"
    assert listing.source_id == "3239722405"
    assert listing.property_type == "flat"
    assert listing.rent_eur == 680
    assert listing.charges_eur == 50
    assert listing.rooms == 1
    assert listing.bedrooms == 1
    assert listing.surface_m2 == 32.0
    assert listing.city == "Saint-Denis"
    assert listing.agency_or_owner == "arobase"
    assert listing.image_url == "https://img.leboncoin.fr/large.jpg?rule=ad-large"
    assert listing.published_at == "2026-07-26 16:37:18"


def test_real_mixed_batch_produces_nonzero_listings_and_filters_non_residential() -> None:
    residential = real_piotrv1001_apartment()
    land = real_piotrv1001_apartment()
    land["listId"] = 3239722406
    land["attributes"] = dict(land["attributes"], real_estate_type="Terrain")
    parking = real_piotrv1001_apartment()
    parking["listId"] = 3239722407
    parking["attributes"] = dict(parking["attributes"], real_estate_type="Parking")
    out = rms._leboncoin_listings([residential, land, parking])
    assert len(out) == 1
    assert out[0].source_id == "3239722405"


def test_maps_native_residential_apartment() -> None:
    listing = rms._map_leboncoin_item(native_apartment())
    assert listing is not None
    assert listing.source_site == "leboncoin"
    assert listing.source_id == "2712345678"
    assert listing.property_type == "flat"
    assert listing.rent_eur == 850
    assert listing.rooms == 3
    assert listing.surface_m2 == 65.0
    assert listing.city == "Saint-Denis"
    assert listing.image_url == "https://img.leboncoin.fr/a.jpg"
    assert listing.published_at == "2026-07-28T10:00:00Z"


def test_maps_native_residential_house_type_1() -> None:
    item = native_apartment()
    item["list_id"] = 42
    item["attributes"] = [{"key": "real_estate_type", "value": "1", "value_label": "Maison"}]
    listing = rms._map_leboncoin_item(item)
    assert listing is not None
    assert listing.property_type == "house"
    assert listing.source_id == "42"


def test_filters_non_residential_land_and_parking() -> None:
    for value, label in (("3", "Terrain"), ("4", "Parking")):
        item = native_apartment()
        item["attributes"] = [{"key": "real_estate_type", "value": value, "value_label": label}]
        assert rms._map_leboncoin_item(item) is None


def test_supports_flattened_item_shape_and_label_type() -> None:
    flat = {
        "id": "998877",
        "title": "Studio meublé",
        "description": "Studio",
        "url": "https://www.leboncoin.fr/ad/locations/998877",
        "price": 600,
        "city": "Sainte-Marie",
        "real_estate_type": "Appartement",
        "zipcode": "97438",
        "rooms": "1",
        "surface": "28",
        "image_url": "https://img/x.jpg",
    }
    listing = rms._map_leboncoin_item(flat)
    assert listing is not None
    assert listing.source_id == "998877"
    assert listing.property_type == "flat"
    assert listing.rent_eur == 600
    assert listing.surface_m2 == 28.0
    assert listing.city == "Sainte-Marie"


def test_actor_input_is_incremental_and_uses_handoff_slugs() -> None:
    payload = rms._leboncoin_actor_input(40)
    assert payload["max_pages"] == 1
    assert payload["limit_per_page"] == 10
    assert payload["max_age_days"] == 30
    source = MOD_PATH.read_text(encoding="utf-8")
    assert "source_status[fname]={'ok': bool(listings)" in source
    assert [u.split("locations=", 1)[1].split("&", 1)[0] for u in payload["urls_list"]] == [
        "Saint-Denis_97400", "Sainte-Marie_97438",
        "Sainte-Suzanne_97441", "Saint-André_97440",
    ]
    assert payload["proxyConfiguration"]["apifyProxyCountry"] == "FR"



def test_rejects_same_named_mainland_city_from_real_scrapifier_shape() -> None:
    item = native_apartment()
    item["list_id"] = 3248933958
    item["location"] = {"city": "Saint-Denis", "zipcode": "93200", "region_name": "Ile-de-France"}
    assert rms._map_leboncoin_item(item) is None


def test_accepts_real_scrapifier_reunion_location() -> None:
    item = native_apartment()
    item["list_id"] = 3241181296
    item["location"] = {"city": "Saint-Denis", "zipcode": "97490", "region_name": "La Reunion"}
    listing = rms._map_leboncoin_item(item)
    assert listing is not None
    assert listing.source_id == "3241181296"

def test_listings_helper_filters_and_maps_mixed_dataset() -> None:
    residential = native_apartment()
    land = native_apartment()
    land["list_id"] = 7
    land["attributes"] = [{"key": "real_estate_type", "value": "3", "value_label": "Terrain"}]
    out = rms._leboncoin_listings([residential, land, "not-a-dict", None])
    assert [l.source_site for l in out] == ["leboncoin"]
    assert out[0].source_id == "2712345678"


def test_raw_writes_can_be_isolated_with_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IMMO_RAW_DIR", str(tmp_path))
    spec2 = importlib.util.spec_from_file_location("rms_scraper_isolated", MOD_PATH)
    assert spec2 is not None and spec2.loader is not None
    mod = importlib.util.module_from_spec(spec2)
    sys.modules["rms_scraper_isolated"] = mod
    spec2.loader.exec_module(mod)

    listing = mod._map_leboncoin_item(native_apartment())
    assert listing is not None
    assert listing.raw_json_path is not None
    assert Path(listing.raw_json_path).parent == tmp_path
    assert (tmp_path / "leboncoin_2712345678.json").exists()


def test_apify_usage_report_writes_dataset_count_and_cost(tmp_path, monkeypatch) -> None:
    report = tmp_path / "apify_usage.jsonl"
    monkeypatch.setattr(rms, "APIFY_USAGE_REPORT", str(report))
    monkeypatch.setenv("APIFY_TOKEN", "token-not-logged")
    monkeypatch.delenv("APIFY_LEBONCOIN_DATASET_ID", raising=False)
    calls = []

    def fake_apify_json(url, token, payload=None, timeout=180):
        calls.append((url, payload))
        assert token == "token-not-logged"
        if "/runs" in url:
            return {"id": "run123", "status": "SUCCEEDED", "defaultDatasetId": "ds123", "usageTotalUsd": 0.42}
        if "/datasets/ds123/items" in url:
            return [native_apartment()]
        raise AssertionError(url)

    monkeypatch.setattr(rms, "_apify_json", fake_apify_json)
    out = rms.scrape_leboncoin_apify_dataset()

    assert len(out) == 1
    rows = [json.loads(line) for line in report.read_text().splitlines()]
    assert rows == [{
        "actor": rms.DEFAULT_LEBONCOIN_ACTOR,
        "cost_usd": 0.42,
        "dataset_id": "ds123",
        "mode": "actor_run",
        "provider": "apify",
        "result_count": 1,
        "run_id": "run123",
        "source": "leboncoin",
        "ts": rows[0]["ts"],
    }]
    assert "token-not-logged" not in report.read_text()
    assert len(calls) == 2


def test_actor_run_must_succeed_and_return_items(monkeypatch) -> None:
    monkeypatch.setenv("APIFY_TOKEN", "token-not-logged")
    monkeypatch.delenv("APIFY_LEBONCOIN_DATASET_ID", raising=False)

    def failed_run(url, token, payload=None, timeout=180):
        assert "/runs" in url
        return {"id": "run-failed", "status": "FAILED", "defaultDatasetId": "ds-failed"}

    monkeypatch.setattr(rms, "_apify_json", failed_run)
    try:
        rms.scrape_leboncoin_apify_dataset()
    except RuntimeError as exc:
        assert "status=FAILED" in str(exc)
    else:
        raise AssertionError("a failed Apify actor must not be reported as successful")

    calls = []

    def empty_dataset(url, token, payload=None, timeout=180):
        calls.append(url)
        if "/runs" in url:
            return {"id": "run-empty", "status": "SUCCEEDED", "defaultDatasetId": "ds-empty"}
        return []

    monkeypatch.setattr(rms, "_apify_json", empty_dataset)
    try:
        rms.scrape_leboncoin_apify_dataset()
    except RuntimeError as exc:
        assert "dataset empty" in str(exc)
    else:
        raise AssertionError("an empty Apify dataset must not be reported as successful")
    assert len(calls) == 2

def main() -> int:
    for test in [
        test_maps_real_piotrv1001_item_shape,
        test_real_mixed_batch_produces_nonzero_listings_and_filters_non_residential,
        test_maps_native_residential_apartment,
        test_maps_native_residential_house_type_1,
        test_filters_non_residential_land_and_parking,
        test_supports_flattened_item_shape_and_label_type,
        test_actor_input_is_incremental_and_uses_handoff_slugs,
        test_listings_helper_filters_and_maps_mixed_dataset,
    ]:
        test()
    print("LEBONCOIN_APIFY_MAPPING PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
