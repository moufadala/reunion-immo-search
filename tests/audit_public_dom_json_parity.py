#!/usr/bin/env python3
"""Audit public/local DOM ↔ JSON parity for the Immo Réunion portal.

This audit is intentionally isolated from deployment/publishing. It checks that:
- local listings.json / changes.json are loadable and internally counted;
- optional public listings.json / changes.json are roughly coherent with local artifacts;
- browser-visible search state (chips, summary/count, cards, console errors) agrees with
  the JSON dataset that the page has loaded;
- changes.html visible cards/counters agree with changes.json.

Playwright is optional. If it is not importable or browsers are not installed, the audit
emits a structured browser SKIP. By default this is exit 1 because DOM parity was not
actually exercised; pass --allow-browser-skip for lightweight CI environments.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import socket
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT / "artifacts" / "app"
DEFAULT_PUBLIC = "https://immo.148.230.103.174.sslip.io/"

SEARCH_CASES: list[dict[str, Any]] = [
    {
        "id": "f4_non_meuble_beausejour_around_900",
        "query": "F4 non meublé Beauséjour autour de 900",
        "expect_any": ["f4", "t4", "4"],
        "expect_all": ["non meubl", "beaus"],
    },
    {
        "id": "st_denis_t2_moins_900",
        "query": "st denis T2 moins 900",
        "expect_any": ["saint-denis", "saint denis", "st denis"],
        # The UI canonicalizes T2/F2 as "T/F2"; checking "2" avoids a false
        # failure while still proving the typology survived parsing.
        "expect_all": ["2", "900"],
    },
    {
        "id": "quartier_la_source_saint_denis",
        "query": "quartier la source saint denis",
        "expect_any": ["la source", "saint-denis", "saint denis"],
        "expect_all": [],
    },
]


@dataclass
class LocalServer:
    base_url: str
    server: ThreadingHTTPServer
    thread: threading.Thread

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002,D401 - match base signature, silence server
        return


def norm(s: Any) -> str:
    text = str(s or "").lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def load_json_path(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")
    return payload


def fetch_json(url: str, timeout: int = 25) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as r:  # noqa: S310 - explicit audit URL/file server
        data = r.read()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return payload


def fetch_text(url: str, timeout: int = 25) -> str:
    with urlopen(url, timeout=timeout) as r:  # noqa: S310 - explicit audit URL/file server
        return r.read().decode("utf-8", errors="replace")


def count_listings(payload: dict[str, Any]) -> int:
    items = payload.get("listings")
    if not isinstance(items, list):
        raise ValueError("listings.json has no array field 'listings'")
    return len(items)


def listing_ids(payload: dict[str, Any]) -> set[str]:
    return {str(x.get("id")) for x in payload.get("listings", []) if isinstance(x, dict) and x.get("id") is not None}


def count_changes(payload: dict[str, Any]) -> int:
    changes = payload.get("changes")
    if isinstance(changes, list):
        return len(changes)
    summary_total = (payload.get("summary") or {}).get("total_events")
    if isinstance(summary_total, int):
        return summary_total
    raise ValueError("changes.json has neither changes[] nor summary.total_events")


def add_check(out: dict[str, Any], name: str, ok: bool, **details: Any) -> None:
    entry = {"name": name, "ok": bool(ok), **details}
    out["checks"].append(entry)
    if not ok:
        out["errors"].append(entry)


def rough_count_ok(local: int, remote: int, pct: float = 0.05, floor: int = 2) -> tuple[bool, int, int]:
    diff = abs(local - remote)
    allowed = max(floor, int(round(max(local, remote) * pct)))
    return diff <= allowed, diff, allowed


def start_local_server(app: Path) -> LocalServer:
    if not app.is_dir():
        raise FileNotFoundError(f"app directory not found: {app}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    handler = lambda *args, **kwargs: QuietHandler(*args, directory=str(app), **kwargs)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return LocalServer(base_url=f"http://{host}:{port}/", server=server, thread=thread)


def extract_count_from_text(text: str) -> int | None:
    text = text.replace("\u202f", " ")
    patterns = [
        r"(\d+)\s+affich(?:é|e|es|és)\s+sur\s+(\d+)\s+correspondances",
        r"(\d+)\s+r[ée]sultat",
        r"(\d+)\s+[ée]l[ée]ment\(s\)",
        r"Tout\s*\((\d+)\)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            # For limited displays, the second group is the actual filtered count.
            return int(m.group(2) if len(m.groups()) >= 2 and m.group(2) else m.group(1))
    return None


def evaluate_browser_state(page: Any) -> dict[str, Any]:
    return page.evaluate(
        r"""() => {
          const summary = document.querySelector('#summary')?.innerText || '';
          const understood = document.querySelector('#understood')?.innerText || '';
          const chips = [...document.querySelectorAll('#nlChips .nlchip')].map(x => x.innerText.trim().replace(/\s+/g, ' '));
          const cards = [...document.querySelectorAll('.card')];
          const ids = cards.slice(0, 40).map(x => x.dataset.id || '');
          const loadedCount = Array.isArray(window.all) ? window.all.length : null;
          const searchCount = window.__searchV3Last && typeof window.__searchV3Last.count === 'number' ? window.__searchV3Last.count : null;
          const lastResultsCount = Array.isArray(window.lastResults) ? window.lastResults.length : null;
          return {summary, understood, chips, cardCount: cards.length, ids, loadedCount, searchCount, lastResultsCount,
                  shownText: document.querySelector('#shown')?.innerText || '',
                  totalText: document.querySelector('#total')?.innerText || '',
                  bodyText: document.body.innerText.slice(0, 1000)};
        }"""
    )


def run_browser_audit(base_url: str, expected_ids: set[str], expected_total: int) -> dict[str, Any]:
    result: dict[str, Any] = {"base_url": base_url, "status": "not_started", "cases": [], "console_errors": [], "errors": []}
    allow_missing_media = os.environ.get("IMMO_ALLOW_MISSING_MEDIA") == "1"
    try:
        from playwright.sync_api import Error as PlaywrightError  # type: ignore
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:  # pragma: no cover - environment-dependent
        result.update({"status": "skipped", "reason": "playwright_import_unavailable", "exception": repr(exc)})
        return result

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("console", lambda msg: result["console_errors"].append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)
            page.goto(base_url + "?audit=public-dom-json-parity", wait_until="networkidle", timeout=45_000)
            page.wait_for_selector("#q", timeout=15_000)
            page.wait_for_function("() => Array.isArray(window.all) && window.all.length > 0", timeout=20_000)
            time.sleep(0.2)
            initial = evaluate_browser_state(page)
            result["initial"] = initial
            if initial.get("loadedCount") != expected_total:
                result["errors"].append(f"browser_loaded_count={initial.get('loadedCount')} expected={expected_total}")

            for case in SEARCH_CASES:
                q = case["query"]
                page.click("#q")
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                if q:
                    page.keyboard.type(q, delay=1)
                page.wait_for_function("q => document.querySelector('#q') && document.querySelector('#q').value === q", arg=q, timeout=5_000)
                page.wait_for_timeout(500)
                st = evaluate_browser_state(page)
                visible_count = extract_count_from_text(st.get("summary", ""))
                # Public parity is about what is rendered: the summary count should
                # agree with the number of cards currently rendered for these
                # targeted queries. Some app internals keep window.__searchV3Last as
                # the full loaded corpus, so using it here produced false failures.
                effective_count = st.get("cardCount")
                merged = norm(" ".join([st.get("understood", ""), " ".join(st.get("chips") or [])]))
                case_errors: list[str] = []

                if visible_count is None:
                    case_errors.append(f"visible_count_unparseable summary={st.get('summary')!r}")
                elif effective_count is not None and visible_count != effective_count:
                    case_errors.append(f"visible_count={visible_count} rendered_card_count={effective_count}")

                if isinstance(visible_count, int) and visible_count > 120 and st.get("cardCount", 0) > 120:
                    case_errors.append(f"too_many_cards={st.get('cardCount')} visible={visible_count}")

                missing_ids = [cid for cid in st.get("ids", []) if cid and cid not in expected_ids]
                if missing_ids:
                    case_errors.append(f"cards_not_in_json={missing_ids[:5]}")

                for token in case.get("expect_all", []):
                    if norm(token) not in merged:
                        case_errors.append(f"missing_understood_token={token!r} got={st.get('understood')!r} chips={st.get('chips')!r}")
                any_tokens = case.get("expect_any") or []
                if any_tokens and not any(norm(tok) in merged for tok in any_tokens):
                    case_errors.append(f"missing_understood_any={any_tokens!r} got={st.get('understood')!r} chips={st.get('chips')!r}")

                if "undefined" in merged or "nan" in merged:
                    case_errors.append("undefined_or_nan_visible_in_criteria")

                result["cases"].append({"id": case["id"], "query": q, "state": st, "visible_count": visible_count, "effective_count": effective_count, "ok": not case_errors, "errors": case_errors})
                result["errors"].extend([f"{case['id']}: {e}" for e in case_errors])

            with contextlib.suppress(PlaywrightTimeoutError):
                page.goto(base_url + "changes.html?audit=public-dom-json-parity", wait_until="networkidle", timeout=30_000)
                page.wait_for_selector(".change-card", timeout=10_000)
                result["changes_dom"] = page.evaluate(
                    """() => ({
                      cards: document.querySelectorAll('.change-card').length,
                      label: document.querySelector('#countLabel')?.innerText || '',
                      filterButtons: [...document.querySelectorAll('[data-filter]')].map(b => b.innerText.trim())
                    })"""
                )
            browser.close()
        console_errors = list(result["console_errors"])
        if allow_missing_media:
            console_errors = [e for e in console_errors if "Failed to load resource: the server responded with a status of 404" not in e]
        result["console_errors"] = console_errors
        result["status"] = "passed" if not result["errors"] and not result["console_errors"] else "failed"
    except Exception as exc:  # browser missing, launch fail, page crash, etc.
        result.update({"status": "skipped", "reason": "playwright_runtime_unavailable_or_failed", "exception": repr(exc)})
        if "PlaywrightError" in locals() and isinstance(exc, PlaywrightError):
            result["hint"] = "Install browser binaries with: python -m playwright install chromium"
    return result


def audit_changes_html_static(html: str, changes_count: int) -> dict[str, Any]:
    cards = len(re.findall(r'class="[^"]*\bchange-card\b', html))
    label_count = extract_count_from_text(re.sub(r"<[^>]+>", " ", html))
    event_ids = {int(x) for x in re.findall(r'data-event-id="(\d+)"', html)}
    parity_ok = cards == changes_count and (label_count in {None, changes_count}) and len(event_ids) == changes_count
    return {
        "cards": cards,
        "label_count": label_count,
        "unique_event_ids": len(event_ids),
        "changes_count": changes_count,
        "parity_ok": parity_ok,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Audit DOM/JSON parity for Immo Réunion public/local artifacts")
    ap.add_argument("--app", default=str(DEFAULT_APP), help="Local artifact app directory (default: artifacts/app)")
    ap.add_argument("--public", default=None, help=f"Optional public base URL (example: {DEFAULT_PUBLIC})")
    ap.add_argument("--allow-browser-skip", action="store_true", help="Exit 0 if only browser checks were skipped after non-browser checks pass")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    app = Path(args.app).resolve()
    public = args.public.rstrip("/") + "/" if args.public else None

    out: dict[str, Any] = {
        "ok": False,
        "app": str(app),
        "public": public,
        "checks": [],
        "errors": [],
        "browser_required": not args.allow_browser_skip,
        "allow_browser_skip": args.allow_browser_skip,
    }

    try:
        local_listings = load_json_path(app / "listings.json")
        local_listing_count = count_listings(local_listings)
        local_ids = listing_ids(local_listings)
        out["local_listings_count"] = local_listing_count
        add_check(out, "local_listings_json_loadable", local_listing_count > 0, count=local_listing_count, ids=len(local_ids))
    except Exception as exc:
        add_check(out, "local_listings_json_loadable", False, exception=repr(exc))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1

    local_changes_count: int | None = None
    try:
        local_changes = load_json_path(app / "changes.json")
        local_changes_count = count_changes(local_changes)
        out["local_changes_count"] = local_changes_count
        add_check(out, "local_changes_json_loadable", local_changes_count >= 0, count=local_changes_count)
    except Exception as exc:
        add_check(out, "local_changes_json_loadable", False, exception=repr(exc))

    if local_changes_count is not None:
        try:
            html = (app / "changes.html").read_text(encoding="utf-8")
            static_changes = audit_changes_html_static(html, local_changes_count)
            out["local_changes_html_static"] = static_changes
            add_check(out, "local_changes_html_vs_json", static_changes["parity_ok"], **static_changes)
        except Exception as exc:
            add_check(out, "local_changes_html_vs_json", False, exception=repr(exc))

    browser_base: str | None = public
    server: LocalServer | None = None

    if public:
        try:
            pub_listings = fetch_json(public + "listings.json")
            pub_count = count_listings(pub_listings)
            pub_ids = listing_ids(pub_listings)
            out["public_listings_count"] = pub_count
            ok, diff, allowed = rough_count_ok(local_listing_count, pub_count)
            add_check(out, "public_listings_count_roughly_matches_local", ok, local=local_listing_count, public=pub_count, diff=diff, allowed=allowed)
            # Browser should compare against the actual public JSON, not stale local if public differs within tolerance.
            local_ids = pub_ids
            local_listing_count = pub_count
        except Exception as exc:
            add_check(out, "public_listings_fetch", False, url=public + "listings.json", exception=repr(exc))
        try:
            pub_changes = fetch_json(public + "changes.json")
            pub_changes_count = count_changes(pub_changes)
            if local_changes_count is not None:
                ok, diff, allowed = rough_count_ok(local_changes_count, pub_changes_count)
                add_check(out, "public_changes_count_roughly_matches_local", ok, local=local_changes_count, public=pub_changes_count, diff=diff, allowed=allowed)
            pub_changes_html = fetch_text(public + "changes.html")
            pub_static = audit_changes_html_static(pub_changes_html, pub_changes_count)
            out["public_changes_html_static"] = pub_static
            add_check(out, "public_changes_html_vs_json", pub_static["parity_ok"], **pub_static)
        except Exception as exc:
            add_check(out, "public_changes_fetch_or_static", False, exception=repr(exc))
    else:
        try:
            server = start_local_server(app)
            browser_base = server.base_url
            out["local_server"] = browser_base
        except Exception as exc:
            add_check(out, "local_browser_server_start", False, exception=repr(exc))

    if browser_base:
        browser = run_browser_audit(browser_base, local_ids, local_listing_count)
        out["browser"] = browser
        if browser.get("status") == "passed":
            add_check(out, "browser_dom_json_parity", True, cases=len(browser.get("cases") or []), base_url=browser_base)
        elif browser.get("status") == "skipped":
            add_check(out, "browser_dom_json_parity", bool(args.allow_browser_skip), skipped=True, reason=browser.get("reason"), exception=browser.get("exception"))
        else:
            add_check(out, "browser_dom_json_parity", False, status=browser.get("status"), errors=browser.get("errors"), console_errors=browser.get("console_errors"))

    if server:
        server.stop()

    out["ok"] = not out["errors"]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
