#!/usr/bin/env python3
"""Build the exact pipeline reconciliation report and fail closed on drift."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline_reconciliation_runtime import build_runtime_reconciliation
from src.source_health import CRITICAL_SOURCES


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument("--source-manifests", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--before-db", type=Path)
    parser.add_argument("--expected-source", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    expected_sources = set(args.expected_source) or set(CRITICAL_SOURCES)

    try:
        result = build_runtime_reconciliation(
            feed_path=args.feed,
            manifests_path=args.source_manifests,
            db_path=args.db,
            before_db_path=args.before_db,
            expected_sources=expected_sources,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "run_id": None,
            "errors": [f"reconciliation input unreadable: {type(exc).__name__}: {exc}"],
            "warnings": [],
            "counts": {},
            "source_gate": {"ok": False},
            "report": {},
            "evidence": {
                "feed": str(args.feed),
                "source_manifests": str(args.source_manifests),
                "database": str(args.db),
                "before_db": str(args.before_db) if args.before_db else None,
            },
        }

    _write_atomic(args.out, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
