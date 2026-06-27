#!/usr/bin/env python3
"""Gate: strong duplicate groups are not shown twice in the default public grid.

Non-destructive policy: all source rows stay in listings.json and dedup_groups.json,
but `display_canonical=false` rows must be filtered from the homepage card grid.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path


def norm(s: object) -> str:
    text = str(s or "").lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def fail(msg: str) -> None:
    raise SystemExit(f"PUBLIC_DEDUP_CANONICAL_DISPLAY FAIL {msg}")


def visible(items: list[dict]) -> list[dict]:
    return [x for x in items if x.get("display_canonical") is not False]


def main() -> int:
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/app")
    listings_payload = json.loads((app / "listings.json").read_text(encoding="utf-8"))
    dedup_payload = json.loads((app / "dedup_groups.json").read_text(encoding="utf-8"))
    html = (app / "index.html").read_text(encoding="utf-8", errors="replace")
    items = listings_payload.get("listings") or []
    by_id = {x.get("id"): x for x in items if x.get("id")}
    groups = dedup_payload.get("groups") or []

    if "display_canonical!==false" not in html:
        fail("index.html does not filter display_canonical=false rows")
    if not groups:
        fail("no dedup groups available")

    auto_checked = 0
    hidden_rows = 0
    for g in groups:
        mids = [mid for mid in (g.get("member_ids") or []) if mid in by_id]
        if len(mids) < 2 or g.get("decision") != "auto_duplicate":
            continue
        auto_checked += 1
        canonical = g.get("canonical_id")
        visible_members = [mid for mid in mids if by_id[mid].get("display_canonical") is not False]
        if len(visible_members) != 1:
            fail(f"group {g.get('group_id')} visible_members={visible_members}")
        if canonical and visible_members[0] != canonical:
            fail(f"group {g.get('group_id')} visible canonical {visible_members[0]} != {canonical}")
        for mid in mids:
            x = by_id[mid]
            if not x.get("dedup_group_id") or x.get("canonical_display_id") != visible_members[0]:
                fail(f"missing dedup metadata for {mid}")
            if x.get("display_canonical") is False:
                hidden_rows += 1

    if auto_checked < 10:
        fail(f"too few auto duplicate groups checked: {auto_checked}")
    if hidden_rows < 10:
        fail(f"too few hidden duplicate rows: {hidden_rows}")

    def sm_hay(x: dict) -> str:
        return norm(" ".join(str(x.get(k, "")) for k in ["title", "location", "city", "description", "url"]))

    sm_visible = [x for x in visible(items) if "sainte marie" in sm_hay(x) or "ste marie" in sm_hay(x)]
    sm_by_signature: dict[tuple, list[str]] = {}
    for x in sm_visible:
        sig = (norm(x.get("title")), x.get("price"), x.get("surface"), x.get("rooms"))
        sm_by_signature.setdefault(sig, []).append(str(x.get("id")))
    exact_dups = {sig: ids for sig, ids in sm_by_signature.items() if len(ids) > 1}
    if exact_dups:
        fail(f"exact duplicate signatures still visible in Sainte-Marie: {exact_dups}")

    print(json.dumps({
        "ok": True,
        "items": len(items),
        "visible_default_grid": len(visible(items)),
        "auto_groups_checked": auto_checked,
        "hidden_duplicate_rows": hidden_rows,
        "sainte_marie_visible": len(sm_visible),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
