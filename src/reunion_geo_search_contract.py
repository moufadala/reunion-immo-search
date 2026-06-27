#!/usr/bin/env python3
"""Shared Réunion geo/search grammar contract.

Pure data/helpers used by search-oracle generation and future alert/search code.
Keep this module side-effect free: no file, DB, or network access.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, TypedDict


class BusinessLocation(TypedDict):
    aliases: list[str]
    commune: str
    exact: bool


def norm(s: Any) -> str:
    text = str(s or "").lower().strip()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def variants(label: str) -> list[str]:
    """Return user-typed variants for a Réunion place label."""
    out: list[str] = []
    raw = str(label or "").strip()
    if not raw:
        return out
    base = [raw.lower(), norm(raw), norm(raw).replace(" saint ", " st "), norm(raw).replace(" sainte ", " ste ")]
    expanded: list[str] = []
    for v in base:
        expanded.append(v)
        expanded.append(v.replace("-", " "))
        expanded.append(v.replace(" ", "-"))
        if v.startswith("saint "):
            expanded.append("st " + v[len("saint "):])
            expanded.append("st-" + v[len("saint "):].replace(" ", "-"))
        if v.startswith("sainte "):
            expanded.append("ste " + v[len("sainte "):])
            expanded.append("ste-" + v[len("sainte "):].replace(" ", "-"))
        if "riviere des pluies" in v:
            expanded.append(v.replace("riviere des pluies", "rivieres des pluies"))
        if "rivières des pluies" in v:
            expanded.append(v.replace("rivières des pluies", "rivière des pluies"))
    for v in expanded:
        cleaned = re.sub(r"\s+", " ", v).strip(" -")
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


BUSINESS_LOCATIONS: dict[str, BusinessLocation] = {
    "Rivière des Pluies": {"aliases": ["rivière des pluies", "riviere des pluies", "rivières des pluies", "rivieres des pluies"], "commune": "Sainte-Marie", "exact": True},
    "Beauséjour": {"aliases": ["beauséjour", "beausejour"], "commune": "Sainte-Marie", "exact": True},
    "Grande Montée": {"aliases": ["grande montée", "grande montee", "la grande montée", "la grande montee"], "commune": "Sainte-Marie", "exact": True},
    "Duparc": {"aliases": ["duparc"], "commune": "Sainte-Marie", "exact": True},
    "La Convenance": {"aliases": ["la convenance", "convenance"], "commune": "Sainte-Marie", "exact": True},
    "Les Cafés": {"aliases": ["les cafés", "les cafes", "cafés", "cafes"], "commune": "Sainte-Marie", "exact": True},
    "La Bretagne": {"aliases": ["la bretagne", "bretagne"], "commune": "Saint-Denis", "exact": True},
    "Moufia": {"aliases": ["moufia"], "commune": "Saint-Denis", "exact": True},
    "Camélias": {"aliases": ["camélias", "camelias", "les camelias", "les camélias"], "commune": "Saint-Denis", "exact": True},
    "Domenjod": {"aliases": ["domenjod"], "commune": "Saint-Denis", "exact": True},
    "Chaudron": {"aliases": ["chaudron", "le chaudron"], "commune": "Saint-Denis", "exact": True},
    "Bellepierre": {"aliases": ["bellepierre"], "commune": "Saint-Denis", "exact": True},
    "Montgaillard": {"aliases": ["montgaillard"], "commune": "Saint-Denis", "exact": True},
    "La Source Saint-Denis": {"aliases": ["quartier la source saint denis", "la source saint denis"], "commune": "Saint-Denis", "exact": True},
    "Bois de Nèfles Sainte-Clotilde": {"aliases": ["bois de nefles sainte clotilde", "bois de nèfles sainte-clotilde", "ste clotilde bois de nefles", "sainte clotilde bois de nefles"], "commune": "Saint-Denis", "exact": True},
    "Bois de Nèfles Saint-Paul": {"aliases": ["bois de nefles saint paul", "bois de nèfles saint-paul", "bois de nefles st paul"], "commune": "Saint-Paul", "exact": True},
    "Bagatelle": {"aliases": ["bagatelle"], "commune": "Sainte-Suzanne", "exact": True},
    "Deux Rives": {"aliases": ["deux rives", "les deux rives"], "commune": "Sainte-Suzanne", "exact": True},
    "Cambuston": {"aliases": ["cambuston"], "commune": "Saint-André", "exact": True},
    "Champ Borne": {"aliases": ["champ borne"], "commune": "Saint-André", "exact": True},
    "Ravine Creuse": {"aliases": ["ravine creuse"], "commune": "Saint-André", "exact": True},
}


COMMUNE_ALIASES: dict[str, list[str]] = {
    "Saint-Denis": variants("Saint-Denis"),
    "Sainte-Marie": variants("Sainte-Marie"),
    "Sainte-Suzanne": variants("Sainte-Suzanne"),
    "Saint-André": variants("Saint-André"),
    "Saint-Paul": variants("Saint-Paul"),
    "Saint-Pierre": variants("Saint-Pierre"),
    "Bras-Panon": variants("Bras-Panon"),
    "Saint-Benoît": variants("Saint-Benoît"),
}


NEGATIVE_LOCATION_CONTEXT: dict[str, list[str]] = {
    "Beauséjour": ["saint paul", "st paul", "saint-gilles", "saint gilles", "st gilles"],
    "La Source Saint-Denis": ["source:", "source ", "source_site", "source site"],
}


def business_locations_for_json() -> dict[str, dict[str, Any]]:
    return {
        label: {"aliases": list(spec["aliases"]), "commune": spec["commune"], "exact": spec["exact"]}
        for label, spec in BUSINESS_LOCATIONS.items()
    }
