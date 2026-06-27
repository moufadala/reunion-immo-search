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
    print(f"WAVE3_LOT_B_BROWSER_AUDIT_FAIL: {msg}")
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
    if not (app / "index.html").exists():
        fail(f"missing {app}/index.html")
    port = free_port()
    handler = functools.partial(QuietHandler, directory=str(app.resolve()))
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/index.html"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1366, "height": 900})
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_selector(".card", timeout=20000)
            page.wait_for_timeout(1700)
            metrics = page.evaluate(
                r"""
                () => {
                  const cards=[...document.querySelectorAll('.card')];
                  const newCards=cards.filter(c=>c.classList.contains('wave3New'));
                  const scoreButtons=cards.filter(c=>/^[0-9]+\/100\s+Analyse/.test((c.querySelector('button[data-open]')?.textContent||'').trim()));
                  const locLines=cards.filter(c=>c.querySelector('.wave3LocLine'));
                  const norm=s=>String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
                  let id='';
                  for(const c of locLines){
                    const x=(window.all||all||[]).find(i=>String(i.id)===String(c.dataset.id));
                    const terms=(window.__wave3LotBHomeCardsDetail?.precisionTermsB(x)||[]);
                    if(x && terms.some(t=>norm(x.description).includes(norm(String(t).replace(/^(rue|avenue|chemin|impasse|route|boulevard|quartier|secteur|proche|près de|pres de|à proximité de|a proximite de)\s+/i,''))))) { id=c.dataset.id; break; }
                  }
                  if(!id) id=(locLines[0]||cards[0])?.dataset.id || cards[0]?.dataset.id;
                  if(id) openDetail(id);
                  const analysis=!!document.querySelector('[data-wave3-analysis]');
                  const analysisText=(document.querySelector('[data-wave3-analysis]')?.textContent||'');
                  const marks=[...document.querySelectorAll('#mDesc .wave3DescMark')].map(x=>x.textContent).filter(Boolean);
                  const modalPrice=(document.querySelector('#mPrice')?.textContent||'');
                  return {cards:cards.length,newCards:newCards.length,scoreButtons:scoreButtons.length,locLines:locLines.length,openedId:id,analysis,analysisText,marks,modalPrice};
                }
                """
            )
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()

    if metrics["cards"] < 20:
        fail(f"too few cards rendered: {metrics}")
    if metrics["newCards"] < 1:
        fail(f"no <=3-day cards highlighted: {metrics}")
    if metrics["scoreButtons"] < 20:
        fail(f"score not shown before Analyse on enough cards: {metrics}")
    if metrics["locLines"] < 5:
        fail(f"precision lines missing on cards: {metrics}")
    if not metrics["analysis"] or "Points favorables" not in metrics["analysisText"] or "À vérifier" not in metrics["analysisText"]:
        fail(f"useful analysis box not opened: {metrics}")
    if "/100" not in metrics["modalPrice"]:
        fail(f"modal score missing in price header: {metrics}")
    # A precise card should usually highlight the source term in description. Some source-only labels may not be in desc,
    # so keep this as an operational signal with a minimum of one discreet mark for the selected precise card.
    if len(metrics["marks"]) < 1:
        fail(f"description highlight missing: {metrics}")

    print(json.dumps({"ok": True, "url": url, **metrics}, ensure_ascii=False, indent=2))
    print("WAVE3_LOT_B_BROWSER_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
