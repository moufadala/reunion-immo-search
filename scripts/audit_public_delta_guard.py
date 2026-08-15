#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.public_feed_dedup import deduplicate_public_feed
from src.publication_policy import evaluate_publication


def load_listings(app_dir: Path) -> tuple[list[dict], Path]:
    feed = app_dir / "feed.json"
    path = feed if feed.exists() else app_dir / "listings.json"
    if not path.exists():
        raise SystemExit(f"listings.json missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("listings", data if isinstance(data, list) else [])
    if not isinstance(rows, list):
        raise SystemExit(f"listings payload is not a list/object[listings]: {path}")
    return [r for r in rows if isinstance(r, dict)], path


def _dedup_shape(row: dict) -> dict:
    shaped = dict(row)
    shaped.setdefault("commune", row.get("city"))
    shaped.setdefault("type", row.get("property_type"))
    shaped.setdefault("rent", row.get("rent_eur", row.get("price")))
    if not shaped.get("images"):
        gallery = row.get("local_image_urls")
        if isinstance(gallery, list) and gallery:
            shaped["images"] = gallery
        else:
            image = row.get("local_image_url") or row.get("image_url")
            shaped["images"] = [image] if image else []
    return shaped


def public_product(app_dir: Path) -> tuple[list[dict], Path, dict[str, int]]:
    rows, path = load_listings(app_dir)
    eligible = [_dedup_shape(row) for row in rows if row.get("active") is not False and evaluate_publication(row).eligible]
    visible, dedup = deduplicate_public_feed(eligible)
    return visible, path, dedup





def source_of(row: dict) -> str:
    return str(row.get("source") or row.get("source_site") or "unknown").strip().lower() or "unknown"


def pct_drop(base: int, cand: int) -> float:
    if base <= 0:
        return 0.0 if cand >= 0 else 100.0
    return max(0.0, (base - cand) * 100.0 / base)


def main() -> int:
    ap = argparse.ArgumentParser(description="Refuse a public app candidate if global/source volumes collapse before publication.")
    ap.add_argument("--baseline", type=Path, default=Path("artifacts/app"), help="Current public app directory")
    ap.add_argument("--candidate", type=Path, required=True, help="Candidate app directory to publish")
    ap.add_argument("--max-drop-pct", type=float, default=15.0, help="Maximum allowed global listing drop")
    ap.add_argument("--critical-source", action="append", default=["seloger"], help="Critical source to guard; repeatable")
    ap.add_argument("--max-critical-drop-pct", type=float, default=35.0, help="Maximum allowed drop for each critical source")
    ap.add_argument("--min-critical-count", type=int, default=1, help="Minimum candidate count for critical source when baseline has it")
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    baseline, baseline_input, baseline_dedup = public_product(args.baseline)
    candidate, candidate_input, candidate_dedup = public_product(args.candidate)
    base_count = len(baseline)
    cand_count = len(candidate)
    base_by_source = Counter(source_of(r) for r in baseline)
    cand_by_source = Counter(source_of(r) for r in candidate)

    errors: list[str] = []
    global_drop = pct_drop(base_count, cand_count)
    if base_count and global_drop > args.max_drop_pct:
        errors.append(f"global volume dropped {global_drop:.1f}%: baseline={base_count} candidate={cand_count} max={args.max_drop_pct:.1f}%")

    critical = []
    for src in sorted({s.lower() for s in args.critical_source}):
        b = base_by_source.get(src, 0)
        c = cand_by_source.get(src, 0)
        drop = pct_drop(b, c)
        item = {"source": src, "baseline": b, "candidate": c, "drop_pct": round(drop, 2)}
        critical.append(item)
        if b > 0 and c < args.min_critical_count:
            errors.append(f"critical source {src} disappeared: baseline={b} candidate={c}")
        if b > 0 and drop > args.max_critical_drop_pct:
            errors.append(f"critical source {src} dropped {drop:.1f}%: baseline={b} candidate={c} max={args.max_critical_drop_pct:.1f}%")

    payload = {
        "ok": not errors,
        "baseline": str(args.baseline),
        "candidate": str(args.candidate),
        "baseline_count": base_count,
        "product_boundary": "active_public_policy_dedup",
        "baseline_input": str(baseline_input),
        "candidate_input": str(candidate_input),
        "baseline_dedup": baseline_dedup,
        "candidate_dedup": candidate_dedup,
        "candidate_count": cand_count,
        "global_drop_pct": round(global_drop, 2),
        "max_drop_pct": args.max_drop_pct,
        "source_counts_baseline": dict(sorted(base_by_source.items())),
        "source_counts_candidate": dict(sorted(cand_by_source.items())),
        "critical_sources": critical,
        "errors": errors,
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if errors:
        print("PUBLIC_DELTA_GUARD FAIL", file=sys.stderr)
        return 2
    print("PUBLIC_DELTA_GUARD PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
