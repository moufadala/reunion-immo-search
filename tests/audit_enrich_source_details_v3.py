#!/usr/bin/env python3
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


def patch_urlopen(mod, handler):
    calls = []
    original = mod.urllib.request.urlopen

    def fake_urlopen(req, timeout=25):
        url = req.full_url
        calls.append(url)
        return FakeResponse(handler(url))

    mod.urllib.request.urlopen = fake_urlopen
    return calls, original


def query(url):
    return parse_qs(urlparse(url).query)


def run_case(name, fn):
    try:
        fn()
        print(f"PASS {name}")
    except Exception as e:
        print(f"FAIL {name}: {e!r}")
        raise


def with_fake(handler, fn):
    mod = load_module()
    calls, original = patch_urlopen(mod, handler)
    try:
        return fn(mod, calls)
    finally:
        mod.urllib.request.urlopen = original


def test_exact_id_first():
    def handler(url):
        assert query(url).get("id") == ["12345"]
        return [keldom_item()]

    def check(mod, calls):
        desc, meta = mod.domimmo_description(row())
        assert desc == long_desc()
        assert meta["matched_id"] == "12345"
        assert len(calls) == 1
        assert "id=12345" in calls[0]
        assert "motscles=" not in calls[0]

    with_fake(handler, check)


def test_schema(payload):
    def check(mod, calls):
        desc, meta = mod.domimmo_description(row())
        assert desc == long_desc()
        assert meta["matched_id"] == "12345"
    with_fake(lambda url: payload, check)


def test_false_positive_rejected():
    false_positive = keldom_item(
        id="99999",
        title="Location Villa 5 pièces Saint-Pierre",
        price=1900,
        surface_habitable=120,
        description=long_desc("Grande villa avec piscine"),
    )
    def handler(url):
        return [] if "id=" in url else [false_positive]
    def check(mod, calls):
        desc, meta = mod.domimmo_description(row())
        assert desc == ""
        assert meta["reject"] == "low_match"
        assert meta["best_score"] < 1.75
        assert len(calls) >= 2
    with_fake(handler, check)


def test_footer_cut():
    source_prose = long_desc()
    with_footer = source_prose + "\n\nCette offre de location est proposée par une agence partenaire. Extrait de notre barème."
    def check(mod, calls):
        desc, meta = mod.domimmo_description(row())
        assert desc == source_prose
        assert "Cette offre de location" not in desc
        assert meta["matched_id"] == "12345"
    with_fake(lambda url: [keldom_item(description=with_footer)], check)


def test_should_update_boilerplate():
    mod = load_module()
    existing = "Location Appartement. L'annonce a bien été ajoutée à vos favoris. Annonce publiée le 25/06/2026. Proposée par une agence."
    ok, reason = mod.should_update(existing, long_desc(), title="Location Appartement")
    assert ok is True
    assert reason == "accepted_cleaned_boilerplate"


class FakeLLMClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"content": [{"type": "tool_use", "name": "extract_listing_signals", "input": self.payload}], "usage": {"input_tokens": 10, "output_tokens": 20}}


def test_llm_disabled_without_client():
    mod = load_module()
    fields, meta = mod.llm_extract_listing_signals(row(), long_desc(), client=None)
    assert fields == {}
    assert meta["llm"] == "disabled"


def test_llm_grounding_filter():
    mod = load_module()
    text = "Appartement proche de l'école Joinville, commerces du centre-ville, accès route du Littoral, résidence Les Badamiers."
    client = FakeLLMClient({"quartier_precis": None, "proximites": ["école Joinville", "plage absente", "mer"], "routes_axes": ["route du Littoral"], "points_repere": ["résidence Les Badamiers"]})
    fields, meta = mod.llm_extract_listing_signals(row(description=text), text, client=client)
    assert fields["proximites"] == ["école Joinville"]
    assert fields["routes_axes"] == ["route du Littoral"]
    assert fields["points_repere"] == ["résidence Les Badamiers"]
    assert "plage absente" in meta["rejected_ungrounded"]
    assert "mer" in meta["rejected_ungrounded"]
    assert mod.llm_fields_have_values(fields) is True


def test_extract_tool_payload_openrouter_shape_and_usage_normalization():
    mod = load_module()
    response = {
        "choices": [{"message": {"tool_calls": [{"type": "function", "function": {"name": "extract_listing_signals", "arguments": json.dumps({"quartier_precis": "Technopole"})}}]}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }
    assert mod.extract_tool_payload(response) == {"quartier_precis": "Technopole"}
    usage = mod.response_usage(response)
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 7


def test_default_llm_model_follows_provider_case_insensitive():
    mod = load_module()
    assert mod.default_llm_model("openrouter") == "anthropic/claude-haiku-4.5"
    assert mod.default_llm_model("OpenRouter") == "anthropic/claude-haiku-4.5"
    assert mod.default_llm_model(" anthropic ") == "claude-haiku-4-5-20251001"


def main() -> int:
    run_case("exact_id_first", test_exact_id_first)
    run_case("schema_list", lambda: test_schema([keldom_item()]))
    run_case("schema_items", lambda: test_schema({"items": [keldom_item()]}))
    run_case("schema_data", lambda: test_schema({"data": [keldom_item()]}))
    run_case("schema_object", lambda: test_schema(keldom_item()))
    run_case("false_positive_rejected", test_false_positive_rejected)
    run_case("footer_cut", test_footer_cut)
    run_case("should_update_boilerplate", test_should_update_boilerplate)
    run_case("llm_disabled_without_client", test_llm_disabled_without_client)
    run_case("llm_grounding_filter", test_llm_grounding_filter)
    run_case("openrouter_payload_shape", test_extract_tool_payload_openrouter_shape_and_usage_normalization)
    run_case("default_llm_model_provider", test_default_llm_model_follows_provider_case_insensitive)
    print("ENRICH_SOURCE_DETAILS_V3_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
