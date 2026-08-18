#!/usr/bin/env python3
"""Blocking browser audit of the interactions actually shipped by the V2 portal."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.browser_qa_runtime import launch_chromium

URL = os.environ.get("IMMO_PUBLIC_URL", "https://immo.148.230.103.174.sslip.io/")


def load_feed() -> tuple[bytes, dict]:
    with urllib.request.urlopen(urllib.parse.urljoin(URL, "feed.json"), timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(f"feed HTTP {response.status}")
        payload = response.read()
        return payload, json.loads(payload)


def number(locator) -> int:
    text = locator.inner_text()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        raise AssertionError(f"missing numeric value in {text!r}")
    return int(digits)


def main() -> int:
    feed_bytes, feed = load_feed()
    listings = [item for item in (feed.get("listings") or []) if item.get("active") is True and item.get("residential") is not False]
    by_id = {str(item["id"]): item for item in listings}
    failures: list[str] = []
    evidence: dict[str, object] = {"feed_count": len(listings)}

    with sync_playwright() as p:
        browser = launch_chromium(p)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.route("**/feed.json", lambda route: route.fulfill(status=200, body=feed_bytes, content_type="application/json"))
        page_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        response = page.goto(URL, wait_until="networkidle", timeout=45_000)
        if response is None or response.status != 200:
            failures.append(f"root HTTP status={getattr(response, 'status', None)}")
        page.get_by_test_id("app-root").wait_for(state="visible", timeout=15_000)
        if "Veille locative" not in page.locator("body").inner_text():
            failures.append("V2 identity text missing")

        total = number(page.get_by_test_id("listing-count"))
        cards = page.get_by_test_id("listing-card")
        initial = cards.count()
        ids = cards.evaluate_all("els => els.map(e => e.dataset.listingId)")
        evidence.update({"ui_total": total, "initial_cards": initial})
        if total != len(listings):
            failures.append(f"listing total UI={total} feed={len(listings)}")
        if initial != min(36, total):
            failures.append(f"initial cards={initial}, expected={min(36, total)}")
        if len(ids) != len(set(ids)) or any(item_id not in by_id for item_id in ids):
            failures.append("card IDs are duplicated or absent from feed")

        page.get_by_role("combobox", name="Trier les annonces").select_option("prixAsc")
        all_sorted_ids = [str(item["id"]) for item in sorted(
            listings,
            key=lambda item: item.get("rent") if item.get("rent") is not None else float("inf"),
        )]
        expected_sorted_ids = all_sorted_ids[:36]
        if expected_sorted_ids:
            page.wait_for_function("expected => [...document.querySelectorAll('[data-testid=listing-card]')].map(e => e.dataset.listingId).join('|') === expected.join('|')", arg=expected_sorted_ids)
        sorted_ids = cards.evaluate_all("els => els.map(e => e.dataset.listingId)")
        if sorted_ids != expected_sorted_ids:
            failures.append("rent sort did not render the exact stable feed sequence")

        if total > len(expected_sorted_ids):
            expected_more = min(36, total - len(expected_sorted_ids))
            expected_loaded_ids = all_sorted_ids[:len(expected_sorted_ids) + expected_more]
            page.get_by_role("button", name=f"Afficher {expected_more} de plus").click()
            page.wait_for_function("expected => [...document.querySelectorAll('[data-testid=listing-card]')].map(e => e.dataset.listingId).join('|') === expected.join('|')", arg=expected_loaded_ids)
            loaded_ids = cards.evaluate_all("els => els.map(e => e.dataset.listingId)")
            if loaded_ids != expected_loaded_ids:
                failures.append("load-more did not render the exact next immutable feed page")

        if cards.count():
            cards.first.click(position={"x": 20, "y": 20})
            dialog = page.get_by_role("dialog")
            dialog.wait_for(state="visible")
            page.get_by_role("button", name="Fermer").click()
            dialog.wait_for(state="hidden")

        next_photo = page.get_by_role("button", name="Photo suivante")
        load_more = page.get_by_role("button", name=re.compile(r"Afficher \d+ de plus"))
        while next_photo.count() == 0 and load_more.count():
            load_more.click()
        if next_photo.count() == 0:
            failures.append("no card gallery was rendered from the feed")
        else:
            gallery_card = next_photo.first.locator("xpath=ancestor::*[@data-testid='listing-card']")
            photo = gallery_card.get_by_test_id("card-photo")
            before = photo.get_attribute("src")
            next_photo.first.click()
            expect(photo).not_to_have_attribute("src", before or "")
            after = photo.get_attribute("src")
            if not before or not after or after == before:
                failures.append("card gallery next arrow did not change the photo")
            gallery_card.get_by_role("button", name="Photo précédente").click()
            if before:
                expect(photo).to_have_attribute("src", before)
            if page.get_by_role("dialog").is_visible():
                failures.append("card gallery arrow opened the details dialog")
            evidence["gallery"] = {"before": before, "after": after}

        page.get_by_role("button", name="Sources", exact=True).click()
        page.get_by_test_id("sources-panel").wait_for(state="visible")
        page.get_by_role("button", name="Annonces", exact=True).click()
        page.get_by_test_id("listing-count").wait_for(state="visible")

        failures.extend(f"pageerror: {error}" for error in page_errors)
        failures.extend(f"console: {error}" for error in console_errors if "favicon" not in error.lower())
        evidence.update({"page_errors": page_errors, "console_errors": console_errors[-10:], "browser_version": browser.version})
        browser.close()

    print(json.dumps({"ok": not failures, "url": URL, "failures": failures, "evidence": evidence}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
