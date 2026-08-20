#!/usr/bin/env python3
"""Blocking, read-only photo-gallery gate for a generated public feed."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.photo_gallery import audit_public_galleries, canonicalize_listing_gallery


def _listings(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict) and isinstance(payload.get("listings"), list):
        values = payload["listings"]
    else:
        raise ValueError("feed must be a list or an object with a listings list")
    return [item for item in values if isinstance(item, dict)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, help="Public app root containing thumbs/")
    parser.add_argument("--feed", required=True, help="Generated feed.json to inspect")
    parser.add_argument("--json-out", help="Optional report file; the feed is never modified")
    args = parser.parse_args()

    app = Path(args.app)
    feed = Path(args.feed)
    try:
        listings = _listings(json.loads(feed.read_text(encoding="utf-8")))
        hash_cache: dict[Path, str] = {}
        baseline = audit_public_galleries(listings, app_root=app, hash_cache=hash_cache)
        canonical: list[dict[str, Any]] = []
        listings_changed = 0
        for listing in listings:
            fixed, _ = canonicalize_listing_gallery(
                listing,
                app_root=app,
                hash_cache=hash_cache,
            )
            canonical.append(fixed)
            if fixed.get("image") != listing.get("image") or fixed.get("images") != listing.get("images"):
                listings_changed += 1
        target = audit_public_galleries(canonical, app_root=app, hash_cache=hash_cache)
        result = {
            "ok": baseline["ok"],
            "app": str(app),
            "feed": str(feed),
            "baseline": baseline,
            "canonical_target": target,
            "canonicalization": {
                "listings_changed": listings_changed,
                "removed_url_duplicates": baseline["repeated_urls"],
                "removed_content_duplicates": baseline["repeated_content"],
                "files_deleted": 0,
            },
        }
        rc = 0 if baseline["ok"] else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "app": str(app), "feed": str(feed), "error": f"{type(exc).__name__}: {exc}"}
        rc = 2

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_out:
        Path(args.json_out).write_text(rendered + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
