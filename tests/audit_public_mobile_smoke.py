#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

HOSTS = [
    "https://immo.148.230.103.174.sslip.io/",
    "https://immo.srv1723523.hstgr.cloud/",
]
UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Mobile Safari/537.36"
REQUIRED_PUBLIC_PATHS = [
    "", "listings.json", "listings_index.json", "veille.html", "sources.html", "doublons.html",
    "opportunites.html", "localisation.html", "alertes.html", "robots.txt", "sitemap.xml", "changes.json",
    "source_health.json", "dedup_groups.json", "opportunity.json", "locations.json", "coverage.json",
]


def fetch(url: str, accept: str = "text/html") -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    })
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.status, resp.headers.get("content-type", ""), resp.read(4_000_000)


def check_host(base: str) -> dict[str, object]:
    status, ct, body = fetch(base)
    html = body.decode("utf-8", "replace")
    errors: list[str] = []
    if status != 200:
        errors.append(f"home status {status}")
    if "Recherche immo RUN" not in html:
        errors.append("missing title marker")
    if "<meta name=\"viewport\"" not in html and "name=\"viewport\"" not in html:
        errors.append("missing viewport meta")
    for forbidden in ["Recherche immo Réunion — moteur visuel", "Canonique", "Suspects", "Match strict"]:
        if forbidden in html:
            errors.append(f"forbidden homepage token: {forbidden}")
    for href in ["veille.html", "sources.html", "doublons.html", "opportunites.html", "localisation.html", "alertes.html"]:
        if href not in html:
            errors.append(f"missing nav alias {href}")
    jstatus, jct, jbody = fetch(urljoin(base, "listings.json"), "application/json")
    if jstatus != 200:
        errors.append(f"listings status {jstatus}")
    data = json.loads(jbody.decode("utf-8"))
    listings = data.get("listings") or []
    if len(listings) < 400:
        errors.append(f"too few listings {len(listings)}")
    local_primary = sum(1 for x in listings if x.get("local_image_url"))
    if local_primary < 350:
        errors.append(f"too few local primary photos {local_primary}")
    checked_paths = []
    for path in REQUIRED_PUBLIC_PATHS:
        accept = "application/json" if path.endswith(".json") else "text/html"
        pstatus, pct, pbody = fetch(urljoin(base, path), accept)
        if pstatus != 200:
            errors.append(f"{path or 'home'} status {pstatus}")
        if b"Traceback" in pbody or b"/opt/data" in pbody:
            errors.append(f"technical leak in {path or 'home'}")
        checked_paths.append({"path": path or "index.html", "status": pstatus, "bytes": len(pbody), "content_type": pct})
    if errors:
        raise AssertionError({"host": base, "errors": errors})
    return {
        "host": base,
        "home_bytes": len(body),
        "listings": len(listings),
        "local_primary": local_primary,
        "content_type": ct,
        "json_content_type": jct,
        "checked_paths": checked_paths,
    }


def main() -> int:
    results = [check_host(h) for h in HOSTS]
    print(json.dumps({"ok": True, "ua": UA, "results": results}, ensure_ascii=False, indent=2))
    print("PUBLIC_MOBILE_SMOKE_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
