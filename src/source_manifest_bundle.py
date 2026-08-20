"""Merge the 13 watcher manifests with external SeLoger evidence.

The watcher and SeLoger run at different pipeline steps, so their evidence is
merged only after both collectors have emitted a strict manifest.  Absence,
duplicates, run-id drift, invalid manifests, or any partial critical source
make the consolidated 14-portal gate fail closed.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from src.pipeline_reconciliation import evaluate_source_manifests


CRITICAL_PORTALS = frozenset(
    {
        "97immo",
        "adrezio",
        "alter",
        "bienici",
        "citya",
        "domimmo",
        "fnaim",
        "immo974",
        "leboncoin",
        "locamoi",
        "ofim",
        "seloger",
        "superimmo",
        "zimo",
    }
)


def merge_and_gate_source_manifests(
    base_bundle: Mapping[str, Any],
    external_manifests: Iterable[Mapping[str, Any]],
    *,
    required_sources: frozenset[str] = CRITICAL_PORTALS,
) -> dict[str, Any]:
    run_id = str(base_bundle.get("run_id") or "").strip()
    raw_base = base_bundle.get("sources")
    if not run_id:
        raise ValueError("base manifest bundle requires a non-empty run_id")
    if not isinstance(raw_base, list):
        raise ValueError("base manifest bundle requires a sources array")

    sources = [
        dict(item)
        for item in [*raw_base, *list(external_manifests)]
        if isinstance(item, Mapping)
    ]
    names = [str(item.get("source") or "").strip() for item in sources]
    counts = Counter(names)
    duplicates = sorted(
        source for source, count in counts.items() if source and count > 1
    )
    present = {source for source in names if source}
    missing = sorted(required_sources - present)
    unexpected = sorted(present - required_sources)
    mismatches = sorted(
        {
            str(item.get("source") or "unknown")
            for item in sources
            if str(item.get("run_id") or "").strip() != run_id
        }
    )

    evaluated = evaluate_source_manifests(
        sources,
        critical_sources=required_sources,
    )
    errors = list(evaluated["errors"])
    errors.extend(
        f"{source}: run_id mismatch (expected {run_id})"
        for source in mismatches
    )
    errors.extend(
        f"{source}: duplicate source manifest"
        for source in duplicates
        if not any(
            error == f"{source}: duplicate source manifest" for error in errors
        )
    )
    errors.extend(
        f"{source}: missing required source manifest" for source in missing
    )
    errors.extend(
        f"{source}: unexpected source manifest" for source in unexpected
    )
    blocking = set(evaluated["blocking_sources"])
    blocking.update(missing)
    blocking.update(mismatches)
    blocking.update(duplicates)
    blocking.update(unexpected)

    gate = {
        **evaluated,
        "ok": not blocking and not errors,
        "blocking_sources": sorted(blocking),
        "missing_sources": missing,
        "duplicate_sources": duplicates,
        "run_id_mismatch_sources": mismatches,
        "unexpected_sources": unexpected,
        "errors": errors,
        "required_source_count": len(required_sources),
        "present_required_source_count": len(required_sources & present),
    }
    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sorted(sources, key=lambda item: str(item.get("source") or "")),
        "gate": gate,
    }
