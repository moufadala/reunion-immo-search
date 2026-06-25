#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": str(exc), "_path": str(path)}


def rows_from_app(app: Path) -> list[dict]:
    data = load_json(app / "listings.json", {"listings": []})
    rows = data.get("listings", data if isinstance(data, list) else [])
    return [r for r in rows if isinstance(r, dict)]


def source_of(row: dict) -> str:
    return str(row.get("source") or row.get("source_site") or "unknown").strip().lower() or "unknown"


def pct(n: int, d: int) -> float:
    return round(n * 100.0 / d, 1) if d else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate daily immo observability summary JSON/Markdown from current public app artifacts.")
    ap.add_argument("--app", type=Path, default=Path("artifacts/app"))
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()

    app = args.app
    out_dir = args.out_dir or (Path("artifacts/daily_summaries") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = rows_from_app(app)
    total = len(rows)
    by_source = Counter(source_of(r) for r in rows)
    with_img = sum(1 for r in rows if r.get("local_image_url") or r.get("image_url"))
    with_local_img = sum(1 for r in rows if r.get("local_image_url") or str(r.get("image_url") or "").startswith("thumbs/"))
    with_desc = sum(1 for r in rows if str(r.get("description") or "").strip())
    with_price = sum(1 for r in rows if r.get("price") or r.get("rent_eur"))
    with_city = sum(1 for r in rows if r.get("city") or r.get("commune"))
    source_health = load_json(app / "source_health.json", {})
    dedup = load_json(app / "dedup_audit.json", {})
    coverage = load_json(app / "coverage.json", {})
    changes = load_json(app / "changes.json", {})

    warnings = []
    if total < 500:
        warnings.append(f"listing_count_low:{total}")
    if by_source.get("seloger", 0) <= 0:
        warnings.append("seloger_missing")
    if pct(with_local_img, total) < 90:
        warnings.append(f"local_image_coverage_low:{pct(with_local_img,total)}%")

    source_health_summary = source_health.get("summary") if isinstance(source_health, dict) else {}
    if not isinstance(source_health_summary, dict):
        source_health_summary = {}

    status_counts = source_health.get("status_counts") if isinstance(source_health, dict) else None
    if status_counts is None:
        status_counts = source_health_summary.get("status_counts")

    payload = {
        "ok": not warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app": str(app),
        "total_listings": total,
        "source_counts": dict(sorted(by_source.items())),
        "coverage": {
            "image_any": {"count": with_img, "pct": pct(with_img, total)},
            "image_local": {"count": with_local_img, "pct": pct(with_local_img, total)},
            "description": {"count": with_desc, "pct": pct(with_desc, total)},
            "price": {"count": with_price, "pct": pct(with_price, total)},
            "city": {"count": with_city, "pct": pct(with_city, total)},
        },
        "source_health_status_counts": status_counts,
        "dedup": {k: dedup.get(k) for k in ["duplicate_groups", "cross_source_groups", "duplicate_rows", "cross_source_rows"]},
        "changes_summary": changes.get("summary") if isinstance(changes, dict) else None,
        "coverage_file_summary": coverage.get("summary") if isinstance(coverage, dict) else None,
        "warnings": warnings,
    }

    json_path = out_dir / "summary.json"
    md_path = out_dir / "summary.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Daily immo summary",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- App: `{app}`",
        f"- Listings: {total}",
        f"- OK: {payload['ok']}",
        f"- Warnings: {', '.join(warnings) if warnings else 'none'}",
        "",
        "## Sources",
    ]
    for src, count in sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- {src}: {count}")
    lines += ["", "## Coverage"]
    for key, val in payload["coverage"].items():
        lines.append(f"- {key}: {val['count']} ({val['pct']}%)")
    if payload["dedup"]:
        lines += ["", "## Dedup"]
        for key, val in payload["dedup"].items():
            lines.append(f"- {key}: {val}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "summary_json": str(json_path), "summary_md": str(md_path), "warnings": warnings}, ensure_ascii=False))
    print("DAILY_SUMMARY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
