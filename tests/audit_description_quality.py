#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from statistics import median

BOILERPLATE_MARKERS = [
    "L'annonce a bien été ajoutée à vos favoris",
    "Annonce publiée le",
    "Proposée par",
    "Cette annonce vous est proposée par",
    "Extrait de notre barème",
]


def load_items(app: Path) -> list[dict]:
    p = app / "listings.json"
    if not p.exists():
        raise SystemExit(f"listings.json missing: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    items = data.get("listings") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise SystemExit(f"invalid listings payload in {p}")
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit public description quality for the Réunion immo clean portal.")
    ap.add_argument("app", nargs="?", default=os.environ.get("IMMO_APP_PATH", "artifacts/app"))
    ap.add_argument("--min-source", type=int, default=int(os.environ.get("IMMO_MIN_SOURCE_DESCRIPTIONS", "580")))
    ap.add_argument("--max-fallback", type=int, default=int(os.environ.get("IMMO_MAX_FALLBACK_DESCRIPTIONS", "5")))
    ap.add_argument("--max-boilerplate", type=int, default=int(os.environ.get("IMMO_MAX_BOILERPLATE_DESCRIPTIONS", "0")))
    ap.add_argument("--max-empty", type=int, default=int(os.environ.get("IMMO_MAX_EMPTY_DESCRIPTIONS", "0")))
    args = ap.parse_args()

    app = Path(args.app)
    items = load_items(app)
    statuses = Counter(str(x.get("description_status") or "") for x in items)
    desc_lens = [len(str(x.get("description") or "").strip()) for x in items]
    empty = [x for x in items if not str(x.get("description") or "").strip()]
    fallback = [x for x in items if "Synth" in str(x.get("description_status") or "")]
    source = [x for x in items if str(x.get("description_status") or "") == "Description source"]
    no_analysis = [x for x in items if not x.get("description_analysis")]
    boilerplate = []
    for x in items:
        text = str(x.get("description") or "")
        hits = [m for m in BOILERPLATE_MARKERS if m in text]
        if hits:
            boilerplate.append({"id": x.get("id"), "source": x.get("source"), "markers": hits})

    report = {
        "ok": True,
        "app": str(app),
        "listings": len(items),
        "description_status": dict(statuses),
        "source_descriptions": len(source),
        "fallback_descriptions": len(fallback),
        "empty_descriptions": len(empty),
        "description_analysis": len(items) - len(no_analysis),
        "lengths": {
            "min": min(desc_lens) if desc_lens else 0,
            "median": median(desc_lens) if desc_lens else 0,
            "max": max(desc_lens) if desc_lens else 0,
        },
        "fallback_ids": [
            {"id": x.get("id"), "source": x.get("source"), "title": x.get("title"), "desc_len": len(str(x.get("description") or "").strip())}
            for x in fallback[:20]
        ],
        "boilerplate": boilerplate[:20],
        "thresholds": {
            "min_source": args.min_source,
            "max_fallback": args.max_fallback,
            "max_boilerplate": args.max_boilerplate,
            "max_empty": args.max_empty,
        },
    }

    failures = []
    if len(source) < args.min_source:
        failures.append(f"source descriptions below threshold: {len(source)} < {args.min_source}")
    if len(fallback) > args.max_fallback:
        failures.append(f"fallback descriptions above threshold: {len(fallback)} > {args.max_fallback}")
    if len(empty) > args.max_empty:
        failures.append(f"empty descriptions above threshold: {len(empty)} > {args.max_empty}")
    if len(boilerplate) > args.max_boilerplate:
        failures.append(f"boilerplate descriptions above threshold: {len(boilerplate)} > {args.max_boilerplate}")
    if no_analysis:
        failures.append(f"missing description_analysis: {len(no_analysis)}")

    if failures:
        report["ok"] = False
        report["failures"] = failures
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    print("DESCRIPTION_QUALITY_AUDIT PASS " + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
