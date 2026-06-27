#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path


def norm_text(s: object) -> str:
    s = str(s or "").lower()
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def band(value: object, step: int) -> str:
    try:
        v = float(str(value))
    except Exception:
        return "?"
    if v <= 0:
        return "?"
    return str(int(round(v / step) * step))


def load_rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("listings", data if isinstance(data, list) else [])
    return [r for r in rows if isinstance(r, dict)]


def default_qa_out(name: str) -> Path:
    """Return a temp report path so duplicate audits do not dirty artifacts/app."""
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(tempfile.gettempdir()) / f"immo-qa-{stamp}" / name


def source_of(row: dict) -> str:
    return str(row.get("source") or row.get("source_site") or "unknown").strip().lower() or "unknown"


def duplicate_key(row: dict) -> str:
    city = norm_text(row.get("city") or row.get("commune") or row.get("location"))
    typ = norm_text(row.get("type") or row.get("property_type") or row.get("property_type_normalized"))
    rent = band(row.get("price") or row.get("rent_eur"), 25)
    surface = band(row.get("surface_m2") or row.get("surface"), 5)
    rooms = str(row.get("rooms") or "?")
    title = norm_text(row.get("title"))[:40]
    return "|".join([city, typ, rent, surface, rooms, title])


def main() -> int:
    ap = argparse.ArgumentParser(description="Non-destructive duplicate audit for public immo listings.")
    ap.add_argument("--listings", type=Path, default=Path("artifacts/app/listings.json"))
    ap.add_argument("--json-out", type=Path, default=default_qa_out("dedup_audit.json"))
    ap.add_argument("--md-out", type=Path, default=default_qa_out("dedup_audit.md"))
    ap.add_argument("--min-group-size", type=int, default=2)
    args = ap.parse_args()

    rows = load_rows(args.listings)
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = str(row.get("duplicate_key") or duplicate_key(row))
        if key and key.count("|") >= 2:
            groups[key].append(row)

    dup_groups = []
    for key, vals in groups.items():
        if len(vals) >= args.min_group_size:
            sources = sorted({source_of(v) for v in vals})
            urls = [v.get("url") for v in vals if v.get("url")][:5]
            dup_groups.append({
                "key": key,
                "size": len(vals),
                "sources": sources,
                "cross_source": len(sources) > 1,
                "sample_titles": [str(v.get("title") or "")[:140] for v in vals[:5]],
                "sample_urls": urls,
            })
    dup_groups.sort(key=lambda g: (not g["cross_source"], -g["size"], g["key"]))
    cross = [g for g in dup_groups if g["cross_source"]]
    payload = {
        "ok": True,
        "listings": str(args.listings),
        "total_rows": len(rows),
        "duplicate_groups": len(dup_groups),
        "cross_source_groups": len(cross),
        "duplicate_rows": sum(g["size"] for g in dup_groups),
        "cross_source_rows": sum(g["size"] for g in cross),
        "top_groups": dup_groups[:50],
        "note": "Audit non destructif: ne supprime aucune annonce; sert à décider une canonicalisation future.",
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# Audit déduplication immo — non destructif",
        "",
        f"- Total annonces: {len(rows)}",
        f"- Groupes doublons/similaires: {len(dup_groups)}",
        f"- Groupes inter-sources: {len(cross)}",
        f"- Lignes dans groupes: {payload['duplicate_rows']}",
        "",
        "## Top groupes",
    ]
    for g in dup_groups[:20]:
        md.append(f"- size={g['size']} cross_source={g['cross_source']} sources={', '.join(g['sources'])} key=`{g['key']}`")
        for t in g["sample_titles"][:3]:
            md.append(f"  - {t}")
    args.md_out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ["ok", "total_rows", "duplicate_groups", "cross_source_groups", "duplicate_rows", "cross_source_rows"]}, ensure_ascii=False))
    print("DEDUP_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
