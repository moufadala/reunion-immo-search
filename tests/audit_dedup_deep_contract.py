#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path


def norm(s: object) -> str:
    text = str(s or "").lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def norm_url(s: object) -> str:
    raw = str(s or "").strip()
    if not raw:
        return ""
    return norm(raw.split("#", 1)[0].split("?", 1)[0].rstrip("/"))


def fail(msg: str) -> None:
    print(f"DEDUP_DEEP_CONTRACT FAIL {msg}")
    raise SystemExit(1)


def audit_db(db: Path) -> dict:
    if not db.exists():
        return {"db_checked": False, "reason": f"missing {db}"}
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "listing_product_enrichment" not in tables:
        return {"db_checked": False, "reason": "missing listing_product_enrichment"}
    rows = con.execute(
        """
        SELECT duplicate_key,
               COUNT(*) AS n,
               SUM(CASE WHEN is_canonical=1 THEN 1 ELSE 0 END) AS canon_n,
               SUM(CASE WHEN is_canonical=0 THEN 1 ELSE 0 END) AS noncanon_n
        FROM listing_product_enrichment
        GROUP BY duplicate_key
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    bad = [dict(r) for r in rows if r["canon_n"] != 1]
    if bad:
        fail(f"DB groups without exactly one canonical: {bad[:5]}")
    broken = con.execute(
        """
        SELECT COUNT(*) c
        FROM listing_product_enrichment e
        LEFT JOIN listing_product_enrichment c
          ON c.source_site=e.canonical_source_site AND c.source_id=e.canonical_source_id
        WHERE c.source_site IS NULL
        """
    ).fetchone()["c"]
    if broken:
        fail(f"DB broken canonical pointers: {broken}")
    return {
        "db_checked": True,
        "db_duplicate_groups": len(rows),
        "db_noncanonical_rows": sum(int(r["noncanon_n"] or 0) for r in rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app", nargs="?", default="artifacts/app")
    ap.add_argument("--db", default="/opt/data/data/reunion_watch.db")
    args = ap.parse_args()
    app = Path(args.app)
    listings_payload = json.loads((app / "listings.json").read_text(encoding="utf-8"))
    dedup_payload = json.loads((app / "dedup_groups.json").read_text(encoding="utf-8"))
    html = (app / "index.html").read_text(encoding="utf-8", errors="replace")
    items = listings_payload.get("listings") or []
    groups = dedup_payload.get("groups") or []
    by_id = {str(x.get("id")): x for x in items if x.get("id")}
    if not items:
        fail("empty listings")
    if dedup_payload.get("version") != "dedup-deep-v2-conservative-nondestructive":
        fail(f"unexpected dedup version: {dedup_payload.get('version')}")
    if "display_canonical!==false" not in html:
        fail("homepage does not filter display_canonical=false")
    if dedup_payload.get("policy") and "non destructive" not in dedup_payload.get("policy"):
        fail("dedup policy is not explicit")

    auto = [g for g in groups if g.get("decision") == "auto_duplicate"]
    review = [g for g in groups if g.get("decision") == "needs_review"]
    if len(auto) < 5:
        fail(f"too few strong auto duplicate groups: {len(auto)}")
    if len(review) < 1:
        fail("no needs_review groups; ambiguous matches are likely being over-hidden")

    hidden = 0
    visible = [x for x in items if x.get("display_canonical") is not False]
    for g in groups:
        mids = [str(mid) for mid in (g.get("member_ids") or []) if str(mid) in by_id]
        if len(mids) < 2:
            fail(f"group {g.get('group_id')} has fewer than 2 exported members")
        if len(g.get("links") or []) != len(mids):
            fail(f"group {g.get('group_id')} links/member mismatch")
        if not g.get("pair_details"):
            fail(f"group {g.get('group_id')} missing pair evidence")
        if "aucune suppression" not in str(g.get("policy") or "") and "non destructive" not in str(g.get("policy") or ""):
            fail(f"group {g.get('group_id')} missing non destructive policy")
        canonical = str(g.get("canonical_id") or "")
        if canonical not in mids:
            fail(f"group {g.get('group_id')} canonical missing from members")
        visible_members = [mid for mid in mids if by_id[mid].get("display_canonical") is not False]
        if g.get("decision") == "auto_duplicate":
            if visible_members != [canonical]:
                fail(f"auto group {g.get('group_id')} visible_members={visible_members} canonical={canonical}")
            hidden += len(mids) - 1
        elif g.get("decision") == "needs_review":
            # Ambiguous pairs must stay inspectable in the public data/UI path.
            if not visible_members:
                fail(f"needs_review group {g.get('group_id')} fully hidden")
        for mid in mids:
            x = by_id[mid]
            if x.get("canonical_display_id") != canonical:
                fail(f"{mid} missing canonical_display_id {canonical}")
            if x.get("dedup_group_id") != g.get("group_id"):
                fail(f"{mid} missing dedup_group_id")
            if x.get("dedup_decision") != g.get("decision"):
                fail(f"{mid} missing dedup_decision")

    url_visible: dict[str, list[str]] = {}
    for x in visible:
        u = norm_url(x.get("url"))
        if u:
            url_visible.setdefault(u, []).append(str(x.get("id")))
    exact_url_dups = {u: ids for u, ids in url_visible.items() if len(ids) > 1}
    if exact_url_dups:
        fail(f"same source URL still visible more than once: {list(exact_url_dups.items())[:5]}")

    db_report = audit_db(Path(args.db))
    print(json.dumps({
        "ok": True,
        "items": len(items),
        "visible_default_grid": len(visible),
        "groups": len(groups),
        "auto_duplicate_groups": len(auto),
        "needs_review_groups": len(review),
        "hidden_auto_duplicate_rows": hidden,
        **db_report,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
