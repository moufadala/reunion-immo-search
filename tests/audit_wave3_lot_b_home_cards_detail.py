#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def fail(msg: str) -> None:
    print(f"WAVE3_LOT_B_HOME_CARDS_DETAIL_AUDIT_FAIL: {msg}")
    raise SystemExit(1)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def parse_age_hours(value: object, generated: datetime) -> float | None:
    s = str(value or "").strip()
    if not s:
        return None
    m = re.search(r"il y a\s*(\d+)\s*(min|mn|h|j|jour|jours|sem)", s, re.I)
    if m:
        n = int(m.group(1)); u = m.group(2).lower()
        if u in {"min", "mn"}: return n / 60
        if u == "h": return float(n)
        if u.startswith("j"): return float(n * 24)
        if u.startswith("sem"): return float(n * 24 * 7)
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return (generated - d).total_seconds() / 3600
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (generated - d).total_seconds() / 3600
    except ValueError:
        return None


def precision_terms(item: dict) -> list[str]:
    li = item.get("location_intelligence") or {}
    out = []
    for v in (li.get("district_best"), li.get("precise_location_label"), item.get("district"), item.get("location")):
        if v and norm(str(v)) != norm(str(item.get("city") or "")):
            out.append(str(v))
    desc = str(item.get("description") or "")
    patterns = [
        r"\b(?:rue|avenue|av\.?|chemin|impasse|allée|allee|route|bd|boulevard)\s+[A-ZÀ-ÖØ-Ýa-zà-öø-ÿ0-9'’ -]{3,45}",
        r"\b(?:quartier|secteur)\s+[A-ZÀ-ÖØ-Ýa-zà-öø-ÿ0-9'’ -]{3,38}",
        r"\b(?:proche|près de|pres de|à proximité de|a proximite de|proximité immédiate des?|proximite immediate des?)\s+[A-ZÀ-ÖØ-Ýa-zà-öø-ÿ0-9'’ -]{3,42}",
    ]
    for pat in patterns:
        out += [m.group(0).strip(" .,;:") for m in re.finditer(pat, desc)]
    seen = set(); clean = []
    for t in out:
        k = norm(t)
        if k and k not in seen and k != norm(str(item.get("city") or "")):
            seen.add(k); clean.append(t)
    return clean


def main() -> int:
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/app")
    index = app / "index.html"
    listings_path = app / "listings.json"
    changes = app / "changes.html"
    alertes = app / "alertes.html"
    if not index.exists() or not listings_path.exists():
        fail("missing index.html or listings.json")
    html = index.read_text(encoding="utf-8")
    data = json.loads(listings_path.read_text(encoding="utf-8"))
    items = data.get("listings") or []
    if len(items) < 400:
        fail(f"too few listings: {len(items)}")

    required = [
        'script id="wave3LotBHomeCardsDetail"',
        "Wave 3 Lot B: homepage new cards + useful analysis + local precision",
        "card.wave3New",
        "wave3FreshBadge",
        "Nouveau ·",
        "seen_last_at",
        "wave3ScorePrefix",
        "Analyse utile",
        "Analyse construite depuis prix, surface, score, photos",
        "precisionTermsB",
        "wave3LocLine",
        "wave3DescMark",
        "highlightDescB",
        "data-wave3-analysis",
    ]
    for token in required:
        if token not in html:
            fail(f"missing token: {token}")
    if html.count('script id="wave3LotBHomeCardsDetail"') != 1:
        fail("wave3 Lot B script must appear exactly once")
    if html.count("Wave 3 Lot B: homepage new cards + useful analysis + local precision") != 1:
        fail("wave3 Lot B CSS must appear exactly once")

    # Guard task scope: patch is homepage-only and must not inject into changes or alerts pages.
    for page in (changes, alertes):
        if page.exists() and "wave3LotBHomeCardsDetail" in page.read_text(encoding="utf-8"):
            fail(f"wave3 script leaked into {page.name}")

    generated_raw = data.get("generated_at") or datetime.now(timezone.utc).isoformat()
    generated = datetime.fromisoformat(str(generated_raw).replace("Z", "+00:00"))
    recent = [x for x in items if (age := parse_age_hours(x.get("published_at"), generated)) is not None and -12 <= age <= 72]
    if len(recent) < 20:
        fail(f"not enough <=3-day listings to make new-card UX meaningful: {len(recent)}")
    if not any(str(x.get("published_at") or "").lower().find("il y a 3 j") >= 0 for x in recent):
        fail("fixture lacks explicit 'il y a 3 j' listing coverage")
    if not all(x.get("seen_last_at") for x in recent[:10]):
        fail("recent listing activity timestamp coverage missing")

    scored = [x for x in items if isinstance((x.get("opportunity_analysis") or {}).get("score"), (int, float))]
    if len(scored) < 400:
        fail(f"opportunity score coverage regressed: {len(scored)}")
    if not re.search(r"\$\{escB\(s\)\}/100</span> Analyse", html):
        fail("Analyse button does not put score before Analyse")

    precise = [x for x in items if precision_terms(x)]
    street = [x for x in precise if any(re.search(r"\b(rue|avenue|chemin|impasse|route|boulevard)\b", t, re.I) for t in precision_terms(x))]
    prox = [x for x in precise if any(re.search(r"\b(proche|proxim|quartier|secteur)\b", t, re.I) for t in precision_terms(x))]
    if len(precise) < 120:
        fail(f"too few listings expose card precision candidates: {len(precise)}")
    if not street:
        fail("no street-level candidate found for rue highlighting")
    if not prox:
        fail("no quartier/proximity candidate found")

    print(json.dumps({
        "ok": True,
        "listings": len(items),
        "recent_3_days": len(recent),
        "scored": len(scored),
        "precision_candidates": len(precise),
        "street_candidates": len(street),
        "proximity_candidates": len(prox),
        "script_once": True,
        "scope_guard": "index-only; changes/alertes untouched by wave3 marker",
    }, ensure_ascii=False, indent=2))
    print("WAVE3_LOT_B_HOME_CARDS_DETAIL_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
