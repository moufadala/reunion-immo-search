#!/usr/bin/env python3
"""Blocking browser audit of the V2 Mouvements tab against feed truth."""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

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


def age_seconds(value: object, now: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (now - parsed).total_seconds()
    except ValueError:
        return None


def value(locator) -> int:
    text = locator.locator("span").first.inner_text()
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits or "0")


def main() -> int:
    feed_bytes, feed = load_feed()
    listings = feed.get("listings") or []
    events = (feed.get("movements") or {}).get("events") or []
    audit_now = datetime.now(timezone.utc)
    recent = [event for event in events if (age := age_seconds(event.get("event_at"), audit_now)) is not None and 0 <= age <= 7 * 86400]
    expected = {
        "movement-online": sum(item.get("active") is True for item in listings),
        "movement-new": sum(event.get("event_type") == "new" for event in recent),
        "movement-withdrawn": sum(event.get("event_type") == "disappeared" for event in recent),
        "movement-reappeared": sum(event.get("event_type") == "reappeared" for event in recent),
    }
    failures: list[str] = []
    observed: dict[str, int] = {}

    with sync_playwright() as p:
        browser = launch_chromium(p)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script(f"Date.now = () => {int(audit_now.timestamp() * 1000)}")
        page.route("**/feed.json", lambda route: route.fulfill(status=200, body=feed_bytes, content_type="application/json"))
        page_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        response = page.goto(URL, wait_until="networkidle", timeout=45_000)
        if response is None or response.status != 200:
            failures.append(f"root HTTP status={getattr(response, 'status', None)}")
        page.get_by_test_id("app-root").wait_for(state="visible", timeout=15_000)
        page.get_by_role("button", name="Mouvements", exact=True).click()
        page.get_by_test_id("movement-online").wait_for(state="visible")

        for testid, count in expected.items():
            observed[testid] = value(page.get_by_test_id(testid))
            if observed[testid] != count:
                failures.append(f"{testid}: UI={observed[testid]} feed={count}")

        event_map = {"new": "movement-new", "disappeared": "movement-withdrawn", "reappeared": "movement-reappeared"}
        for event_type, counter in event_map.items():
            section = page.locator(f'[data-event-type="{event_type}"]')
            section.wait_for(state="visible")
            links = section.get_by_role("link").count()
            if links != min(8, expected[counter]):
                failures.append(f"{event_type}: rendered links={links}, expected={min(8, expected[counter])}")

        page.get_by_role("button", name="Annonces", exact=True).click()
        page.get_by_test_id("listing-count").wait_for(state="visible")
        failures.extend(f"pageerror: {error}" for error in page_errors)
        failures.extend(f"console: {error}" for error in console_errors if "favicon" not in error.lower())
        browser_version = browser.version
        browser.close()

    print(json.dumps({"ok": not failures, "url": URL, "expected": expected, "observed": observed, "failures": failures, "browser_version": browser_version}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
