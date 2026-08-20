"""Strict acquisition evidence for the SeLoger CDP collector.

This module is deliberately browser-free so pagination evidence and manifests
can be tested without Chrome.  A collector must name why it stopped.  Only a
reported-total match, a genuinely short last page, or an explicit empty page
can prove exhaustion; caps, stuck pagination, missing buttons, and errors stay
partial/failed.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


IDENTITY_REDUCTION_REASONS = {"duplicate_id", "missing_id"}


@dataclass(frozen=True, slots=True)
class SelogerCollectionEvidence:
    page_sizes: tuple[int, ...]
    unique_ids: int
    reported_total: int | None
    pages_attempted: int
    pages_succeeded: int
    complete: bool
    terminal_reason: str
    truncation_signals: list[str]
    error: str | None
    page_size: int

    @property
    def fetched_items(self) -> int:
        return sum(self.page_sizes)

    @property
    def last_page_short(self) -> bool:
        return bool(self.page_sizes) and self.page_sizes[-1] < self.page_size


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def extract_reported_total(text: str | None) -> int | None:
    """Extract the result count from a French SeLoger result heading."""
    if not text:
        return None
    match = re.search(
        r"([0-9][0-9\s\u00a0\u202f]*)\s*(?:annonces?|biens?|r[ée]sultats?)\b",
        str(text),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    digits = re.sub(r"[^0-9]", "", match.group(1))
    return int(digits) if digits else None


def evaluate_seloger_collection(
    *,
    page_sizes: Iterable[int],
    unique_ids: int,
    reported_total: int | None,
    terminal_reason: str,
    pages_attempted: int | None = None,
    pages_succeeded: int | None = None,
    error: str | None = None,
    page_size: int = 30,
) -> SelogerCollectionEvidence:
    sizes = tuple(_non_negative_int(value, "page size") for value in page_sizes)
    unique = _non_negative_int(unique_ids, "unique_ids")
    expected = (
        None
        if reported_total is None
        else _non_negative_int(reported_total, "reported_total")
    )
    succeeded = (
        len(sizes)
        if pages_succeeded is None
        else _non_negative_int(pages_succeeded, "pages_succeeded")
    )
    attempted = (
        succeeded
        if pages_attempted is None
        else _non_negative_int(pages_attempted, "pages_attempted")
    )
    nominal_size = _non_negative_int(page_size, "page_size")
    if nominal_size == 0:
        raise ValueError("page_size must be positive")
    if succeeded > attempted:
        raise ValueError("pages_succeeded cannot exceed pages_attempted")
    if len(sizes) != succeeded:
        raise ValueError("one page size is required for every successful page")
    if unique > sum(sizes):
        raise ValueError("unique_ids cannot exceed fetched cards")

    reason = str(terminal_reason or "").strip() or "unknown_terminal"
    signals: list[str] = []
    complete = False

    if error:
        signals.append("page_error")
    elif succeeded != attempted:
        signals.append(f"page_failure:{succeeded}/{attempted}")
    elif expected is not None:
        if unique == expected:
            complete = True
            reason = "reported_total_reached"
        elif unique < expected:
            signals.append(f"reported_total_gap:{unique}/{expected}")
        else:
            signals.append(f"reported_total_overflow:{unique}/{expected}")
    elif reason == "short_page" and sizes and 0 < sizes[-1] < nominal_size:
        complete = True
    elif reason == "empty_page" and sizes and sizes[-1] == 0:
        complete = True
    else:
        signals.append(reason)

    return SelogerCollectionEvidence(
        page_sizes=sizes,
        unique_ids=unique,
        reported_total=expected,
        pages_attempted=attempted,
        pages_succeeded=succeeded,
        complete=complete,
        terminal_reason=reason,
        truncation_signals=list(dict.fromkeys(signals)),
        error=str(error).strip() if error else None,
        page_size=nominal_size,
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_unique_ids(path: Path) -> tuple[int | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"artifact_invalid:{type(exc).__name__}"
    if not isinstance(payload, dict) or not isinstance(payload.get("annonces"), list):
        return None, "artifact_invalid:annonces_not_array"
    identifiers: set[str] = set()
    missing = 0
    for item in payload["annonces"]:
        if not isinstance(item, dict):
            missing += 1
            continue
        identifier = str(item.get("id") or "").strip()
        if not identifier:
            missing += 1
            continue
        identifiers.add(identifier)
    if missing:
        return len(identifiers), f"artifact_missing_ids:{missing}"
    return len(identifiers), None


def build_seloger_manifest(
    evidence: SelogerCollectionEvidence,
    *,
    run_id: str,
    artifact_path: Path,
    normalized_ids: set[str],
    event_statuses: Iterable[str],
    rejection_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    rejections = {
        str(reason): _non_negative_int(count, f"rejection {reason}")
        for reason, count in (rejection_counts or {}).items()
    }
    signals = list(evidence.truncation_signals)
    error = evidence.error
    status = (
        "complete"
        if evidence.complete
        else ("failed" if evidence.pages_succeeded == 0 and error else "partial")
    )

    artifact_sha: str | None = None
    artifact_unique: int | None = None
    if not artifact_path.is_file():
        signals.append("artifact_missing")
    else:
        artifact_sha = sha256_file(artifact_path)
        artifact_unique, artifact_error = _artifact_unique_ids(artifact_path)
        if artifact_error:
            signals.append(artifact_error)
        if artifact_unique is not None and artifact_unique != evidence.unique_ids:
            signals.append(
                f"artifact_unique_id_mismatch:{artifact_unique}/{evidence.unique_ids}"
            )
    if status == "complete" and signals:
        status = "partial"

    normalized_count = len(normalized_ids)
    if normalized_count > evidence.unique_ids:
        raise ValueError("normalized ids cannot exceed collected unique ids")
    post_unique_rejections = {
        str(reason): int(count) for reason, count in rejections.items()
        if reason not in IDENTITY_REDUCTION_REASONS and int(count) > 0
    }
    normalized_rejections = sum(post_unique_rejections.values())
    accounted = normalized_count + normalized_rejections
    if accounted > evidence.unique_ids:
        raise ValueError("normalized ids and rejections exceed collected unique ids")
    if accounted < evidence.unique_ids:
        gap = evidence.unique_ids - accounted
        rejections["unaccounted_unique_id"] = (
            rejections.get("unaccounted_unique_id", 0) + gap
        )
        normalized_rejections += gap
        post_unique_rejections["unaccounted_unique_id"] = gap
        signals.append(f"normalization_accounting_gap:{gap}")
        if status == "complete":
            status = "partial"

    statuses = [str(value).strip().lower() for value in event_statuses]
    inserted = sum(value in {"new", "inserted"} for value in statuses)
    updated = sum(value in {"changed", "updated", "reappeared"} for value in statuses)
    reappeared = sum(value == "reappeared" for value in statuses)
    if inserted + updated > normalized_count:
        raise ValueError("event statuses exceed normalized items")
    unchanged = normalized_count - inserted - updated

    if status == "partial" and not signals and not error:
        signals.append(evidence.terminal_reason or "unproven_terminal")
    if status == "failed" and not error:
        error = "collection failed before any successful page"
    expected_count = evidence.reported_total
    if expected_count is None and evidence.complete:
        expected_count = evidence.unique_ids

    identity_gap = evidence.fetched_items - evidence.unique_ids
    pre_unique_rejections = (
        {"identity_reduction": identity_gap} if identity_gap else {}
    )
    return {
        "run_id": str(run_id),
        "source": "seloger",
        "status": status,
        "attempted": evidence.pages_attempted > 0,
        "pages_attempted": evidence.pages_attempted,
        "pages_succeeded": evidence.pages_succeeded,
        "fetched_items": evidence.fetched_items,
        "parsed_items": evidence.fetched_items,
        "unique_ids": evidence.unique_ids,
        "normalized_items": normalized_count,
        "rejected_items": normalized_rejections,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "withdrawn": 0,
        "reappeared": reappeared,
        "expected_count": expected_count,
        "dataset_id": None,
        "retries": 0,
        "truncation_signals": list(dict.fromkeys(signals)),
        "error": error,
        "terminal_reason": evidence.terminal_reason,
        "page_sizes": list(evidence.page_sizes),
        "last_page_short": evidence.last_page_short,
        "reported_total": evidence.reported_total,
        "rejected_items_by_reason": dict(sorted(post_unique_rejections.items())),
        "pre_unique_rejections_by_reason": pre_unique_rejections,
        "unparsed_items_by_reason": {},
        "seen_ids": sorted(str(value) for value in normalized_ids),
        "artifact_path": str(artifact_path),
        "artifact_sha256": artifact_sha,
        "artifact_unique_ids": artifact_unique,
        "event_counts_stage": "collection",
    }
