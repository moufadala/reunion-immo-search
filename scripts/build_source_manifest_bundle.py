#!/usr/bin/env python3
"""Merge watcher and external source manifests, then gate all 14 portals."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.source_manifest_bundle import merge_and_gate_source_manifests


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot read JSON {path}: {type(exc).__name__}: {exc}") from exc


def _external_items(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("sources"), list):
        return [dict(item) for item in payload["sources"] if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [dict(payload)]
    raise ValueError(f"external manifest {path} must be an object or bundle")


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--external", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    base = _read_json(args.base)
    if not isinstance(base, dict):
        raise ValueError("base manifest bundle must be a JSON object")
    external: list[dict[str, Any]] = []
    for path in args.external:
        external.extend(_external_items(path))
    bundle = merge_and_gate_source_manifests(base, external)
    _atomic_json_write(args.out, bundle)
    print(
        json.dumps(
            {
                "ok": bundle["gate"]["ok"],
                "blocking_sources": bundle["gate"]["blocking_sources"],
                "missing_sources": bundle["gate"]["missing_sources"],
                "out": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return 0 if bundle["gate"]["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
