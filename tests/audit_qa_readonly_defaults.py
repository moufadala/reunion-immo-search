#!/usr/bin/env python3
"""P0 contract: QA/audit commands must be read-only by default.

The scripts can still write explicit --json-out/--md-out paths, but default
report paths must not point inside the product repository's artifacts tree.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = [
    ("tests/run_product_quality_matrix.py", "parse_args", []),
    ("tests/audit_semantic_source_quality_matrix.py", "parse_args", []),
    ("scripts/audit_dedup_public.py", None, []),
]


def load_module(rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_safe_output(path_value: object, label: str, errors: list[str]) -> None:
    p = Path(str(path_value))
    s = str(p)
    if not p.is_absolute():
        errors.append(f"{label}: default output is relative ({s}); expected /tmp/immo-qa-* or explicit absolute temp path")
        return
    try:
        p.relative_to(ROOT)
        errors.append(f"{label}: default output is inside repo ({s}); QA defaults must be read-only")
    except ValueError:
        pass
    if "immo-qa-" not in s:
        errors.append(f"{label}: default output should be run-scoped under immo-qa-* ({s})")


def main() -> int:
    errors: list[str] = []

    product = load_module("tests/run_product_quality_matrix.py")
    args = product.parse_args([])
    assert_safe_output(args.json_out, "run_product_quality_matrix --json-out", errors)
    assert_safe_output(args.md_out, "run_product_quality_matrix --md-out", errors)

    semantic = load_module("tests/audit_semantic_source_quality_matrix.py")
    args = semantic.parse_args([])
    assert_safe_output(args.json_out, "audit_semantic_source_quality_matrix --json-out", errors)
    assert_safe_output(args.md_out, "audit_semantic_source_quality_matrix --md-out", errors)

    dedup = load_module("scripts/audit_dedup_public.py")
    assert_safe_output(dedup.default_qa_out("dedup_audit.json"), "audit_dedup_public --json-out", errors)
    assert_safe_output(dedup.default_qa_out("dedup_audit.md"), "audit_dedup_public --md-out", errors)

    if errors:
        print("QA_READONLY_DEFAULTS FAIL")
        for err in errors:
            print(f" - {err}")
        return 1
    print("QA_READONLY_DEFAULTS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
