#!/usr/bin/env python3
from __future__ import annotations

import json
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

BASE = "https://immo.148.230.103.174.sslip.io/"
MIN_LISTINGS = 500
MIN_SELOGER = 150
TIMEOUT = 25


def fetch(path: str) -> tuple[int, str, bytes]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "immo-public-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        return r.status, r.headers.get("content-type", ""), r.read()


def main() -> int:
    errors: list[str] = []
    evidence: dict[str, object] = {"checked_at": datetime.now(timezone.utc).isoformat(), "base": BASE}
    try:
        status, ctype, body = fetch("")
        evidence["index_status"] = status
        evidence["index_content_type"] = ctype
        if status != 200:
            errors.append(f"index HTTP {status}")
        if b"Recherche immo RUN" not in body and b"portail propre" not in body:
            errors.append("index marker missing")
    except Exception as exc:
        errors.append(f"index fetch failed: {type(exc).__name__}: {exc}")

    try:
        status, ctype, body = fetch("listings.json")
        evidence["listings_status"] = status
        evidence["listings_content_type"] = ctype
        data = json.loads(body.decode("utf-8"))
        rows = data.get("listings") or []
        total = len(rows)
        seloger = sum(1 for x in rows if str(x.get("source", "")).lower() == "seloger")
        local_primary = sum(1 for x in rows if x.get("local_image_url"))
        evidence.update({"total": total, "seloger": seloger, "local_primary": local_primary})
        if total < MIN_LISTINGS:
            errors.append(f"listing volume below threshold: {total} < {MIN_LISTINGS}")
        if seloger < MIN_SELOGER:
            errors.append(f"SeLoger volume below threshold: {seloger} < {MIN_SELOGER}")
        if local_primary < int(total * 0.85):
            errors.append(f"local primary photo coverage low: {local_primary}/{total}")
    except Exception as exc:
        errors.append(f"listings fetch/parse failed: {type(exc).__name__}: {exc}")

    for path in ["veille.html", "sources.html", "doublons.html", "opportunites.html", "localisation.html", "alertes.html"]:
        try:
            status, ctype, _ = fetch(path)
            evidence[path] = status
            if status != 200:
                errors.append(f"{path} HTTP {status}")
        except Exception as exc:
            errors.append(f"{path} failed: {type(exc).__name__}: {exc}")

    if errors:
        print("🚨 Immo public monitor: anomalie détectée")
        print(json.dumps({"ok": False, "errors": errors, "evidence": evidence}, ensure_ascii=False, indent=2))
        return 2
    # Silent success for cron/no_agent watchdogs.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
