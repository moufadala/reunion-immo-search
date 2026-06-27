#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def fail(msg: str) -> None:
    print(f"DETAIL_GEO_PHOTO_PRUDENT_AUDIT_FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/app")
    index = app / "index.html"
    listings_path = app / "listings.json"
    if not index.exists() or not listings_path.exists():
        fail("missing index.html or listings.json")

    html = index.read_text(encoding="utf-8")
    data = json.loads(listings_path.read_text(encoding="utf-8"))
    items = data.get("listings") or []
    if len(items) < 400:
        fail(f"too few listings for durable detail audit: {len(items)}")

    required_tokens = [
        'script id="wave2LotCDetailGeoPhoto"',
        "detailThumbs",
        "detailPhotoFallback",
        "hardenCardBrokenImagesC",
        "Localisation prudente",
        "Adresse exacte non déduite ni inventée",
        "Photos source indisponibles ou cassées",
        "Source:",
        "data-detail-geo-prudent",
    ]
    for token in required_tokens:
        if token not in html:
            fail(f"missing detail UX token: {token}")

    if html.count('script id="wave2LotCDetailGeoPhoto"') != 1:
        fail("wave2 Lot C patch must be idempotent / appear once")

    # Homepage stays light: no extra homepage sections or preloaded maps; CSS may define detail classes,
    # but the visible body before the modal must not contain gallery/map markup.
    body_html = html.split("</style>", 1)[-1]
    main_before_modal = body_html.split('<div class="modal"', 1)[0]
    if "openstreetmap.org/export/embed" in main_before_modal or "detailThumbs" in main_before_modal:
        fail("detail-only map/gallery markup leaked into homepage body")

    local_primary = sum(1 for x in items if x.get("local_image_url"))
    galleries = sum(1 for x in items if len(x.get("local_image_urls") or []) > 1)
    if local_primary < 350:
        fail(f"local primary coverage regressed: {local_primary}")
    if galleries < 100:
        fail(f"gallery coverage regressed: {galleries}")

    missing = []
    for x in items:
        for rel in x.get("local_image_urls") or []:
            rel = str(rel)
            if rel.startswith("thumbs/") and not (app / rel).exists():
                missing.append((x.get("id"), rel))
                if len(missing) >= 5:
                    break
        if len(missing) >= 5:
            break
    if missing:
        fail(f"missing local gallery files: {missing}")

    map_points = [x for x in items if x.get("map_point")]
    if len(map_points) < 50:
        fail(f"too few map points to support detail geography: {len(map_points)}")
    bad_precision = []
    for x in map_points:
        precision = str((x.get("map_point") or {}).get("precision") or "").lower()
        note = str((x.get("geo_quality") or {}).get("note") or "").lower()
        combined = precision + " " + note
        if "adresse exacte" in combined and "pas adresse exacte" not in combined and "sauf adresse" not in combined:
            bad_precision.append((x.get("id"), precision, note))
        if not re.search(r"approxim|quartier|commune|source|lieu-dit", combined):
            bad_precision.append((x.get("id"), precision, note))
        if len(bad_precision) >= 5:
            break
    if bad_precision:
        fail(f"map precision wording is not prudent: {bad_precision}")

    geo_known = sum(1 for x in items if (x.get("geo_quality") or {}).get("level") in {"haute", "moyenne", "commune"})
    if geo_known < 400:
        fail(f"geo quality coverage regressed: {geo_known}")

    print(json.dumps({
        "ok": True,
        "listings": len(items),
        "local_primary": local_primary,
        "galleries": galleries,
        "map_points": len(map_points),
        "geo_known": geo_known,
        "script_once": True,
    }, ensure_ascii=False, indent=2))
    print("DETAIL_GEO_PHOTO_PRUDENT_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
