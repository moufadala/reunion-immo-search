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
    # Public listings may be deliberately slimmed for mobile/homepage budget.  In that
    # case the full explanations live in opportunity.json and the card keeps only a
    # compact opportunity_score.  Accept either shape, but still require broad
    # non-filtering coverage so opportunity remains a ranking/audit signal, not a
    # destructive publication gate.
    annotated = 0
    invalid_scores = []
    for x in listings:
        analysis = x.get("opportunity_analysis") or {}
        score = analysis.get("score") if isinstance(analysis, dict) else None
        if score is None:
            score = x.get("opportunity_score")
        if isinstance(score, (int, float)) and 0 <= score <= 100:
            annotated += 1
        elif len(invalid_scores) < 5:
            invalid_scores.append({"id": x.get("id"), "score": score})
    if annotated < max(500, int(len(listings) * .95)):
        fail(f"too few scored listings: {annotated}/{len(listings)} examples={invalid_scores}")
    top_ids = {str(x.get("id")) for x in top if x.get("id")}
    listing_ids = {str(x.get("id")) for x in listings if x.get("id")}
    missing_top = sorted(top_ids - listing_ids)[:5]
    if missing_top:
        fail(f"opportunity top references non-public listings: {missing_top}")
    print("OPPORTUNITY_V2_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
