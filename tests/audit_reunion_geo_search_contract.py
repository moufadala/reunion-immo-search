#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.reunion_geo_search_contract import BUSINESS_LOCATIONS, COMMUNE_ALIASES, NEGATIVE_LOCATION_CONTEXT, business_locations_for_json, norm, variants


def test_core_variants_cover_reunion_user_spellings() -> None:
    assert "saint denis" in variants("Saint-Denis")
    assert "st denis" in variants("Saint-Denis")
    assert "sainte marie" in variants("Sainte-Marie")
    assert "ste marie" in variants("Sainte-Marie")
    assert "rivieres des pluies" in variants("Rivière des Pluies")


def test_business_locations_include_private_and_dangerous_cases() -> None:
    assert BUSINESS_LOCATIONS["Les Cafés"]["commune"] == "Sainte-Marie"
    assert "les cafes" in [norm(x) for x in BUSINESS_LOCATIONS["Les Cafés"]["aliases"]]
    assert BUSINESS_LOCATIONS["La Source Saint-Denis"]["aliases"] == ["quartier la source saint denis", "la source saint denis"]
    assert "la source" not in BUSINESS_LOCATIONS["La Source Saint-Denis"]["aliases"]
    assert "saint gilles" in NEGATIVE_LOCATION_CONTEXT["Beauséjour"]


def test_commune_aliases_are_available_for_generator_and_alerts() -> None:
    for commune in ["Saint-Denis", "Sainte-Marie", "Sainte-Suzanne", "Saint-André", "Saint-Paul", "Saint-Pierre"]:
        assert commune in COMMUNE_ALIASES
        assert COMMUNE_ALIASES[commune]


def test_oracle_generator_imports_shared_contract() -> None:
    script = (ROOT / "scripts" / "generate_domain_inventory_and_oracle_v2.py").read_text(encoding="utf-8")
    assert "from src.reunion_geo_search_contract import BUSINESS_LOCATIONS, norm, variants" in script
    assert "BUSINESS_LOCATIONS: dict" not in script


def test_business_locations_copy_is_deep_enough() -> None:
    copied = business_locations_for_json()
    copied["Beauséjour"]["aliases"].append("mutation-test")
    assert "mutation-test" not in BUSINESS_LOCATIONS["Beauséjour"]["aliases"]


def test_generator_emits_shared_locations_without_mutating_repo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        oracle_path = tmp_path / "acceptance_search_oracle_v2.json"
        inv_path = tmp_path / "domain_inventory_v2.json"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "generate_domain_inventory_and_oracle_v2.py"),
                "--app",
                str(ROOT / "artifacts" / "app"),
                "--inventory-out",
                str(inv_path),
                "--oracle-out",
                str(oracle_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        queries = {case["query"] for case in oracle["cases"]}
        assert "les cafés" in queries
        assert "les cafes" in queries
        assert "quartier la source saint denis" in queries
        assert "la source" not in queries
        assert inv_path.exists()


def main() -> int:
    for test in [
        test_core_variants_cover_reunion_user_spellings,
        test_business_locations_include_private_and_dangerous_cases,
        test_commune_aliases_are_available_for_generator_and_alerts,
        test_oracle_generator_imports_shared_contract,
        test_business_locations_copy_is_deep_enough,
        test_generator_emits_shared_locations_without_mutating_repo,
    ]:
        test()
    print("REUNION_GEO_SEARCH_CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
