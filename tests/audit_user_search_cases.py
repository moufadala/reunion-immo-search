#!/usr/bin/env python3
"""User-reported natural-search regression cases for the public immo portal.

This audit intentionally checks *behaviour*, not only that natural-search tokens exist.
Run:
  python3 tests/audit_user_search_cases.py
Optional:
  IMMO_PUBLIC_URL=https://immo.148.230.103.174.sslip.io/ python3 tests/audit_user_search_cases.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from playwright.sync_api import sync_playwright

URL = os.environ.get("IMMO_PUBLIC_URL", "https://immo.148.230.103.174.sslip.io/")


def count_from_summary(summary: str) -> int | None:
    m = re.search(r"(?:sur\s+)?(\d+)\s+correspondances|^(\d+)\s+r[ée]sultat", summary or "")
    if not m:
        return None
    return int(next(g for g in m.groups() if g))


def run_query(page, q: str) -> dict:
    page.fill("#q", q)
    page.dispatch_event("#q", "input")
    page.wait_for_timeout(450)
    summary = page.locator("#summary").inner_text()
    chips = [x.strip().replace("×", "") for x in page.locator("#nlChips .nlchip").all_inner_texts()]
    first_cards = page.locator(".card").evaluate_all(
        "els => els.slice(0, 5).map(e => e.innerText)"
    )
    return {"q": q, "summary": summary, "count": count_from_summary(summary), "chips": chips, "first_cards": first_cards}


def listing_payload_has(alias_pattern: str) -> bool:
    """Return whether the currently audited public payload has evidence for an alias.

    Some quartier fixtures are data-dependent: if the daily source no longer has
    an exact Rivière des Pluies row, the correct UX is a helpful zero-result
    state, not a failing expectation for a stale item count.
    """
    try:
        data_url = urllib.parse.urljoin(URL, "listings.json")
        with urllib.request.urlopen(data_url, timeout=20) as r:
            data = json.loads(r.read())
    except Exception:
        return True  # Keep the audit strict if we cannot inspect payload truth.
    rx = re.compile(alias_pattern, re.I)
    for x in data.get("listings") or []:
        hay = " ".join(str(x.get(k, "")) for k in ["title", "location", "city", "description", "url"])
        hay += " " + json.dumps(x.get("location_intelligence") or {}, ensure_ascii=False)
        if rx.search(hay):
            return True
    return False


def main() -> int:
    failures: list[str] = []
    evidence: list[dict] = []
    has_riviere_des_pluies = listing_payload_has(r"rivi[eè]res?\s+des\s+pluies")
    has_beausejour = listing_payload_has(r"beaus[eé]jour")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(URL, wait_until="networkidle", timeout=45_000)
        page.evaluate("localStorage.clear(); sessionStorage.clear();")
        total_text = page.locator("#total").inner_text()
        total = int(re.sub(r"\D", "", total_text) or "0")

        quartier_cases = [
            ("rivière des pluies", 1 if has_riviere_des_pluies else 0),
            ("rivieres des pluies", 1 if has_riviere_des_pluies else 0),
            ("beausejour", 1 if has_beausejour else 0),
            ("grande montée", 1),
            # If the dataset has no exact listing for a quartier, the correct UX is 0 + explanation/suggestion,
            # not silently returning the whole database.
            ("duparc", 0),
            ("bretagne", 0),
        ]
        for q, min_expected_when_known in quartier_cases:
            r = run_query(page, q)
            evidence.append(r)
            if r["count"] == total:
                failures.append(f"{q!r}: renvoie toute la base ({total}) au lieu d'un quartier/commune ou d'un état vide utile")
            if min_expected_when_known and (r["count"] or 0) < min_expected_when_known:
                failures.append(f"{q!r}: devrait trouver au moins {min_expected_when_known} résultat connu, obtenu {r['summary']}")

        t4 = run_query(page, "T4")
        f4 = run_query(page, "F4")
        evidence.extend([t4, f4])
        if not any("T4" in c or "F4" in c or "4" in c for c in f4["chips"]):
            failures.append("F4: aucun chip/critère 4 pièces compris")
        if t4["count"] != f4["count"]:
            failures.append(f"F4/T4: devraient être synonymes, obtenu T4={t4['summary']} vs F4={f4['summary']}")

        non_meuble = run_query(page, "non meublé")
        evidence.append(non_meuble)
        if any(c.strip().lower() == "meublé" for c in non_meuble["chips"]):
            failures.append("'non meublé' est compris comme 'Meublé' au lieu d'une exclusion")
        if any("Meublé" in card for card in non_meuble["first_cards"][:3]):
            failures.append("'non meublé' affiche des cartes marquées Meublé dans les premiers résultats")

        budget = run_query(page, "900")
        evidence.append(budget)
        if any(c.startswith("≤ 900") for c in budget["chips"]):
            failures.append("Budget nu '900': impose ≤900 sans demander/opérateur visible")
        if not page.locator("#q").input_value().strip().startswith(("≤", ">=", "=", "<", ">")):
            # Current desired product contract: a visible operator choice must update the query/criteria line.
            if page.locator("[data-price-op], .priceOp, #priceOp").count() == 0:
                failures.append("Aucun mini-contrôle d'opérateur budget visible pour le nombre nu '900'")
        if page.locator('[data-price-op="around"]').count() == 0:
            failures.append("Budget nu '900': bouton autour absent")
        else:
            page.locator('[data-price-op="around"]').click()
            page.wait_for_timeout(450)
            q_value = page.locator("#q").input_value().strip().lower()
            understood = page.locator("#understood").inner_text().lower()
            if "autour de 900" not in q_value:
                failures.append(f"Bouton autour: champ recherche non mis à jour correctement ({q_value!r})")
            if "autour de 900" not in understood:
                failures.append(f"Bouton autour: ligne critères non synchronisée ({understood!r})")

        # The dangerous UX path is not just a bare number: the operator click must preserve
        # the full natural-language query and update chips/criteria without losing rooms,
        # quartier, or non-meublé exclusion.
        page.fill("#q", "F4 non meublé Beauséjour 900")
        page.dispatch_event("#q", "input")
        page.wait_for_timeout(450)
        combo_before = {
            "q": page.locator("#q").input_value(),
            "understood": page.locator("#understood").inner_text(),
            "chips": page.locator("#nlChips .nlchip").all_inner_texts(),
        }
        if page.locator('[data-price-op="around"]').count() == 0:
            failures.append("Phrase complète + budget nu: bouton autour absent")
        else:
            page.locator('[data-price-op="around"]').click()
            page.wait_for_timeout(450)
            combo_after = {
                "q": page.locator("#q").input_value(),
                "understood": page.locator("#understood").inner_text(),
                "chips": page.locator("#nlChips .nlchip").all_inner_texts(),
                "summary": page.locator("#summary").inner_text(),
            }
            evidence.append({"q": "F4 non meublé Beauséjour 900 + clic autour", "before": combo_before, "after": combo_after})
            qn = combo_after["q"].lower()
            merged = (combo_after["understood"] + " " + " ".join(combo_after["chips"])).lower()
            for expected in ["f4", "non meublé", "beauséjour", "autour de 900"]:
                if expected not in qn:
                    failures.append(f"Phrase complète + autour: le champ ne conserve pas {expected!r} ({combo_after['q']!r})")
            for expected in ["t/f4", "non meublé", "beauséjour", "autour de 900"]:
                if expected not in merged:
                    failures.append(f"Phrase complète + autour: critères/chips ne conservent pas {expected!r} ({merged!r})")

        browser.close()

    print(json.dumps({"ok": not failures, "url": URL, "failures": failures, "evidence": evidence}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
