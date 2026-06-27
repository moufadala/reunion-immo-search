#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
except ModuleNotFoundError:  # CI/lightweight shell may not have Playwright; keep static gate useful.
    sync_playwright = None  # type: ignore[assignment]

FORBIDDEN = re.compile(r"(/opt/data|Traceback|sqlite3\.OperationalError|SECRET_KEY|api_key=|password=|token=|Authorization:|Bearer\s+)", re.I)
REQUIRED_CSP = ["default-src 'self'", "object-src 'none'", "base-uri 'self'", "form-action 'none'"]
REQUIRED_SECTIONS = ["Dernier sprint", "Sources actives", "Alertes dry-run", "Pipeline", "Liens QA"]


def fail(msg: str) -> None:
    print(f"OPS_COCKPIT_BROWSER_STATIC_AUDIT_FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/app")
    html_path = app / "ops.html"
    json_path = app / "ops_status.json"
    index_path = app / "index.html"
    if not html_path.exists() or not json_path.exists():
        fail("ops artifacts missing")
    html = html_path.read_text(encoding="utf-8", errors="replace")
    raw_json = json_path.read_text(encoding="utf-8", errors="replace")
    data = json.loads(raw_json)

    if FORBIDDEN.search(html) or FORBIDDEN.search(raw_json):
        fail("secret/internal token leak")
    if 'name="robots" content="noindex,nofollow,noarchive"' not in html:
        fail("missing noindex,nofollow,noarchive")
    csp = re.search(r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"', html, re.I)
    if not csp:
        fail("missing CSP meta")
    csp_value = csp.group(1)
    missing_csp = [x for x in REQUIRED_CSP if x not in csp_value]
    if missing_csp:
        fail(f"weak CSP: missing {missing_csp}")
    if "<script" in html.lower():
        fail("ops cockpit should remain static/no JS")
    if index_path.exists():
        index = index_path.read_text(encoding="utf-8", errors="replace")
        if "ops.html" in index or "ops_status.json" in index:
            fail("ops linked from homepage")
    if not data.get("qa_links") or not data.get("pipeline") or not data.get("alert_dry_run"):
        fail("missing ops JSON sections")

    browser_checked = False
    metrics = {"links": re.findall(r'href="([^"]+)"', html), "panels": html.count('class="panel"')}
    if sync_playwright is not None:
        url = html_path.resolve().as_uri()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            page.goto(url, wait_until="domcontentloaded")
            title = page.title()
            if "Cockpit ops" not in title:
                fail(f"unexpected title {title!r}")
            text = page.locator("body").inner_text(timeout=5000)
            for section in REQUIRED_SECTIONS:
                if section not in text:
                    fail(f"missing rendered section {section}")
            metrics = page.evaluate("""() => ({
                bodyW: document.body.scrollWidth,
                docW: document.documentElement.clientWidth,
                panels: [...document.querySelectorAll('.panel')].length,
                links: [...document.querySelectorAll('a')].map(a => a.getAttribute('href')),
                robots: document.querySelector('meta[name=robots]')?.content || '',
                csp: document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.content || ''
            })""")
            browser.close()
            browser_checked = True
        if metrics["bodyW"] > metrics["docW"] + 2:
            fail(f"mobile horizontal body overflow {metrics['bodyW']} > {metrics['docW']}")
        if "noindex" not in metrics["robots"] or "nofollow" not in metrics["robots"]:
            fail("robots meta not rendered")
    else:
        for section in REQUIRED_SECTIONS:
            if section not in html:
                fail(f"missing static section {section}")
    if metrics["panels"] < 8:
        fail("not enough rendered ops panels")
    bad_links = [x for x in metrics["links"] if x and not (x.startswith("http") or x.endswith(".html") or x.startswith("?"))]
    if bad_links:
        fail(f"unexpected non-qa links: {bad_links[:5]}")

    print(json.dumps({"ok": True, "app": str(app), "mobile_viewport": "390x844", "browser": browser_checked, "links": len(metrics["links"])}, ensure_ascii=False))
    print("OPS_COCKPIT_BROWSER_STATIC_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
