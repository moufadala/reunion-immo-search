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


def fetch(path: str) -> tuple[int, str, bytes, int | None]:
    """Return (status, content_type, full_body, declared_content_length).

    Always reads the full response body so JSON parses are never attempted on a
    truncated sample.  content_length is the server-declared size (may be None
    if the server does not send Content-Length); callers should compare it
    against len(body) to detect partial transfers.
    """
    ctx = ssl.create_default_context()
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "immo-public-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        body = r.read()
        cl_header = r.headers.get("content-length")
        declared_len = int(cl_header) if cl_header and cl_header.isdigit() else None
        return r.status, r.headers.get("content-type", ""), body, declared_len


def main() -> int:
    errors: list[str] = []
    evidence: dict[str, object] = {"checked_at": datetime.now(timezone.utc).isoformat(), "base": BASE}
    try:
        status, ctype, body, _ = fetch("")
        evidence["index_status"] = status
        evidence["index_content_type"] = ctype
        if status != 200:
            errors.append(f"index HTTP {status}")
        if b"Recherche immo RUN" not in body and b"portail propre" not in body:
            errors.append("index marker missing")
    except Exception as exc:
        errors.append(f"index fetch failed: {type(exc).__name__}: {exc}")

    try:
        status, ctype, body, declared_len = fetch("listings.json")
        evidence["listings_status"] = status
        evidence["listings_content_type"] = ctype
        evidence["listings_bytes_received"] = len(body)
        evidence["listings_content_length_header"] = declared_len
        # Distinguish truncated transfer from corrupt JSON: only attempt parse when
        # the full body was received (or the server does not declare a length).
        if declared_len is not None and len(body) < declared_len:
            errors.append(
                f"listings.json transfer incomplete: received {len(body)} bytes "
                f"but Content-Length declared {declared_len} — "
                "this is a network/server error, not a JSON corruption"
            )
        else:
            evidence["listings_sampled"] = False
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
            status, ctype, _, __ = fetch(path)
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
