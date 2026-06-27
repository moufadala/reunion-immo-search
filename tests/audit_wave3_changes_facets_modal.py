#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import functools
import http.server
import json
import socket
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]


def fail(msg: str) -> None:
    print(f"WAVE3_CHANGES_FACETS_MODAL_AUDIT_FAIL: {msg}")
    raise SystemExit(1)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> int:
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/app")
    changes_json = json.loads((app / "changes.json").read_text(encoding="utf-8"))
    changes = changes_json.get("changes") or []
    if not changes:
        fail("changes.json empty")
    expected_regions = sorted(str((c.get("item") or {}).get("region")) for c in changes if (c.get("item") or {}).get("region"))
    expected_communes = sorted(str((c.get("item") or {}).get("commune")) for c in changes if (c.get("item") or {}).get("commune"))
    expected_zones = sorted(str(z) for c in changes for z in ((c.get("item") or {}).get("zones") or []) if z)
    if not expected_regions:
        fail("no region facets in changes.json")

    port = free_port()
    handler = functools.partial(QuietHandler, directory=str(app.resolve()))
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/changes.html"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_selector(".change-card", timeout=20000)
            metrics = page.evaluate(
                """
                ({expectedRegions, expectedCommunes, expectedZones}) => {
                  const visible = () => [...document.querySelectorAll('.change-card')].filter(c=>!c.hidden);
                  const opts = id => [...document.querySelectorAll(`#${id} option`)].map(o=>o.value).filter(Boolean);
                  const all0 = visible().length;
                  const region = expectedRegions[0] || '';
                  const commune = expectedCommunes[0] || '';
                  const zone = expectedZones[0] || '';
                  const setVal = (id,v) => { const el=document.getElementById(id); if(el && v){ el.value=v; el.dispatchEvent(new Event('input',{bubbles:true})); } };
                  setVal('regionFilter', region);
                  const regionVisible = visible().length;
                  document.getElementById('regionFilter').value=''; document.getElementById('regionFilter').dispatchEvent(new Event('input',{bubbles:true}));
                  setVal('communeFilter', commune);
                  const communeVisible = visible().length;
                  document.getElementById('communeFilter').value=''; document.getElementById('communeFilter').dispatchEvent(new Event('input',{bubbles:true}));
                  if(zone) setVal('zoneFilter', zone);
                  const zoneVisible = visible().length;
                  document.getElementById('zoneFilter').value=''; document.getElementById('zoneFilter').dispatchEvent(new Event('input',{bubbles:true}));
                  const first = visible()[0];
                  first.querySelector('.card-open')?.click();
                  const modalOpen = document.querySelector('#changeModal.open') && !document.querySelector('#changeModal').hidden;
                  const modalText = document.querySelector('#changeModal')?.textContent || '';
                  const hasCycle = /Cycle annonce/.test(modalText);
                  const hasSourceLink = document.querySelector('#modalLink')?.getAttribute('href') || '';
                  return {
                    all0, region, commune, zone,
                    regionOptions: opts('regionFilter'), communeOptions: opts('communeFilter'), zoneOptions: opts('zoneFilter'),
                    regionVisible, communeVisible, zoneVisible,
                    modalOpen: !!modalOpen, hasCycle, modalText: modalText.slice(0,500), hasSourceLink
                  };
                }
                """,
                {"expectedRegions": expected_regions, "expectedCommunes": expected_communes, "expectedZones": expected_zones},
            )
            console_errors = [m.text for m in page.context.pages[0].context.pages[0].context.pages] if False else []
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()

    errors: list[str] = []
    if metrics["all0"] != len(changes):
        errors.append(f"initial visible {metrics['all0']} != changes {len(changes)}")
    if set(expected_regions) - set(metrics["regionOptions"]):
        errors.append("missing region facet options")
    if expected_communes and set(expected_communes[:3]) - set(metrics["communeOptions"]):
        errors.append("missing commune facet options")
    if expected_zones and not metrics["zoneOptions"]:
        errors.append("missing zone facet options")
    if metrics["region"] and not (0 < metrics["regionVisible"] <= len(changes)):
        errors.append(f"bad region filter count: {metrics['regionVisible']}")
    if metrics["commune"] and not (0 < metrics["communeVisible"] <= len(changes)):
        errors.append(f"bad commune filter count: {metrics['communeVisible']}")
    if metrics["zone"] and not (0 < metrics["zoneVisible"] <= len(changes)):
        errors.append(f"bad zone filter count: {metrics['zoneVisible']}")
    if not metrics["modalOpen"] or not metrics["hasCycle"]:
        errors.append(f"modal/cycle missing: {metrics}")
    if not metrics["hasSourceLink"]:
        errors.append("modal source link missing")
    if errors:
        fail(json.dumps({"errors": errors, "metrics": metrics}, ensure_ascii=False, indent=2))
    print(json.dumps({"ok": True, "changes": len(changes), **metrics}, ensure_ascii=False, indent=2))
    print("WAVE3_CHANGES_FACETS_MODAL_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
