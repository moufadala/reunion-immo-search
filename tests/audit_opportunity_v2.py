#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def fail(msg: str) -> None:
    print(f"OPPORTUNITY_V2_AUDIT_FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/app")
    opp = json.loads((app / "opportunity.json").read_text(encoding="utf-8"))
    listings = json.loads((app / "listings.json").read_text(encoding="utf-8"))["listings"]
    if opp.get("score_version") != "opportunity-v2-explainable-2026-06-25":
        fail("wrong score_version")
    top = opp.get("top") or []
    if len(top) < 20:
        fail(f"top too small: {len(top)}")
    required_components = {"market_price", "listing_quality", "location_fit", "freshness", "risk", "data_confidence"}
    for idx, item in enumerate(top[:20]):
        comps = item.get("components") or {}
        if set(comps) != required_components:
            fail(f"components mismatch at top {idx}: {comps}")
        if not item.get("reasons"):
            fail(f"missing reasons at top {idx}")
        if item.get("confidence") not in {"haute", "moyenne", "faible"}:
            fail(f"bad confidence at top {idx}")
        if not isinstance(item.get("reference_sample_size"), int):
            fail(f"missing reference sample size at top {idx}")
    annotated = sum(1 for x in listings if (x.get("opportunity_analysis") or {}).get("score_version") == opp.get("score_version"))
    if annotated < max(500, int(len(listings) * .95)):
        fail(f"too few annotated listings: {annotated}/{len(listings)}")
    print("OPPORTUNITY_V2_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
