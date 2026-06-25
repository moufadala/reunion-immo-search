from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "enrich_source_details_v3.py"


def load_module():
    spec = importlib.util.spec_from_file_location("enrich_source_details_v3", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeHeaders:
    def get_content_charset(self):
        return "utf-8"


class FakeResponse:
    status = 200
    headers = FakeHeaders()

    def __init__(self, payload):
        self.payload = payload

    def read(self, n=-1):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def row(**overrides):
    base = {
        "source_site": "domimmo",
        "source_id": "12345",
        "url": "https://www.domimmo.com/location/appartement/12345",
        "title": "Location Appartement 2 pièces Saint-Denis",
        "city": "Saint-Denis",
        "description": "",
        "raw_json_path": None,
        "rent_eur": 850,
        "surface_m2": 42,
    }
    base.update(overrides)
    return base


def long_desc(prefix="Bel appartement rénové"):
    return (
        f"{prefix}, lumineux et traversant, situé dans une résidence calme "
        "avec cuisine équipée, séjour agréable, chambre climatisée, rangements, "
        "place de stationnement et proximité immédiate des commodités."
    )


def keldom_item(**overrides):
    base = {
        "id": "12345",
        "title": "Location Appartement 2 pièces Saint-Denis",
        "price": 850,
        "surface_habitable": 42,
        "description": long_desc(),
        "photos": ["a.jpg", "b.jpg"],
    }
    base.update(overrides)
    return base


def patch_urlopen(monkeypatch, mod, handler):
    calls = []

    def fake_urlopen(req, timeout=25):
        url = req.full_url
        calls.append(url)
        return FakeResponse(handler(url))

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    return calls


def query(url):
    return parse_qs(urlparse(url).query)


def test_domimmo_queries_keldom_by_exact_id_first_and_stops_on_exact_match(monkeypatch):
    mod = load_module()

    def handler(url):
        qs = query(url)
        assert qs.get("id") == ["12345"]
        return [keldom_item()]

    calls = patch_urlopen(monkeypatch, mod, handler)
    desc, meta = mod.domimmo_description(row())

    assert desc == long_desc()
    assert meta["matched_id"] == "12345"
    assert len(calls) == 1
    assert "id=12345" in calls[0]
    assert "motscles=" not in calls[0]


def test_domimmo_accepts_keldom_json_list_schema(monkeypatch):
    mod = load_module()
    patch_urlopen(monkeypatch, mod, lambda url: [keldom_item()])
    desc, meta = mod.domimmo_description(row())
    assert desc == long_desc()
    assert meta["matched_id"] == "12345"


def test_domimmo_accepts_keldom_json_items_schema(monkeypatch):
    mod = load_module()
    patch_urlopen(monkeypatch, mod, lambda url: {"items": [keldom_item()]})
    desc, meta = mod.domimmo_description(row())
    assert desc == long_desc()
    assert meta["matched_id"] == "12345"


def test_domimmo_accepts_keldom_json_data_schema(monkeypatch):
    mod = load_module()
    patch_urlopen(monkeypatch, mod, lambda url: {"data": [keldom_item()]})
    desc, meta = mod.domimmo_description(row())
    assert desc == long_desc()
    assert meta["matched_id"] == "12345"


def test_domimmo_accepts_keldom_single_object_schema(monkeypatch):
    mod = load_module()
    patch_urlopen(monkeypatch, mod, lambda url: keldom_item())
    desc, meta = mod.domimmo_description(row())
    assert desc == long_desc()
    assert meta["matched_id"] == "12345"


def test_domimmo_rejects_low_score_false_positive(monkeypatch):
    mod = load_module()
    false_positive = keldom_item(
        id="99999",
        title="Location Villa 5 pièces Saint-Pierre",
        price=1900,
        surface_habitable=120,
        description=long_desc("Grande villa avec piscine"),
    )

    def handler(url):
        qs = query(url)
        if "id" in qs:
            return []
        return [false_positive]

    calls = patch_urlopen(monkeypatch, mod, handler)
    desc, meta = mod.domimmo_description(row())

    assert desc == ""
    assert meta["reject"] == "low_match"
    assert meta["best_score"] < 1.75
    assert len(calls) >= 2


def test_domimmo_cuts_agency_footer(monkeypatch):
    mod = load_module()
    source_prose = long_desc()
    with_footer = (
        source_prose
        + "\n\nCette offre de location est proposée par une agence partenaire. "
        + "Extrait de notre barème et mentions légales."
    )
    patch_urlopen(monkeypatch, mod, lambda url: [keldom_item(description=with_footer)])

    desc, meta = mod.domimmo_description(row())

    assert desc == source_prose
    assert "Cette offre de location est proposée" not in desc
    assert "Extrait de notre barème" not in desc
    assert meta["matched_id"] == "12345"


def test_should_update_replaces_existing_boilerplate_with_clean_text():
    mod = load_module()
    existing = (
        "Location Appartement 2 pièces Saint-Denis. "
        "L'annonce a bien été ajoutée à vos favoris. "
        "Annonce publiée le 25/06/2026. Proposée par une agence."
    )
    ok, reason = mod.should_update(
        existing,
        long_desc(),
        title="Location Appartement 2 pièces Saint-Denis",
    )
    assert ok is True
    assert reason == "accepted_cleaned_boilerplate"
