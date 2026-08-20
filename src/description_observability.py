"""Deterministic quality observations for scraped listing descriptions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from typing import Any, Iterable, Literal


DescriptionStatus = Literal[
    "not_attempted",
    "fetched_complete",
    "source_short_complete",
    "fetched_sparse",
    "blocked",
    "error",
    "stale",
]
DescriptionContentState = Literal["missing", "source", "synthetic", "stale"]


@dataclass(frozen=True)
class DescriptionSelection:
    text: str
    content_state: DescriptionContentState
    length: int
    sha256: str | None
    kept_existing: bool
    reason: str
    full_text_evidence: bool




@dataclass(frozen=True)
class DescriptionObservation:
    status: DescriptionStatus
    content_state: DescriptionContentState
    length: int
    sha256: str | None
    extractor_version: str
    attempted_at: datetime | None
    succeeded_at: datetime | None
    extraction_succeeded: bool
    markers: tuple[str, ...]
    http_status: int | None
    error: str | None
    full_text_evidence: bool


def _searchable(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    return " ".join(
        "".join(ch for ch in folded if not unicodedata.combining(ch)).lower().split()
    )


def _quality_markers(text: str, *, minimum_length: int) -> tuple[str, ...]:
    searchable = _searchable(text)
    markers: list[str] = []
    if not text:
        markers.append("empty")
    elif len(text) < minimum_length:
        markers.append("too_short")
    if re.search(r"\b(voir|lire|afficher)\s+plus\b", searchable):
        markers.append("expand_prompt")
    if any(
        phrase in searchable
        for phrase in (
            "annonce seloger collectee par cdp",
            "description generee automatiquement",
            "description non fournie par la source",
        )
    ):
        markers.append("synthetic_fallback")
    if text.rstrip().endswith(("...", "…")):
        markers.append("truncated_ellipsis")
    return tuple(markers)


def assess_description(
    description: str | None,
    *,
    extractor_version: str,
    attempted_at: datetime | None = None,
    succeeded_at: datetime | None = None,
    http_status: int | None = None,
    blocked: bool = False,
    error: str | None = None,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(days=7),
    minimum_length: int = 80,
    full_text_evidence: bool = False,
) -> DescriptionObservation:
    """Classify one extraction attempt while retaining auditable evidence.

    A transport success is deliberately not an extraction success: blank,
    synthetic, or visibly truncated text remains ``fetched_sparse``. A clean
    but genuinely short source text is complete and labelled as such.
    """
    text = " ".join((description or "").split())
    length = len(text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    markers = _quality_markers(text, minimum_length=minimum_length)

    status: DescriptionStatus
    quality_success = False
    blocking_markers = set(markers) - {"too_short"}
    effective_success = succeeded_at
    if attempted_at is None:
        status = "not_attempted"
        effective_success = None
    elif blocked or http_status in {401, 403, 407, 429}:
        status = "blocked"
        effective_success = None
    elif error is not None or (http_status is not None and http_status >= 400):
        status = "error"
        effective_success = None
    elif "too_short" in markers and not full_text_evidence:
        status = "fetched_sparse"
        effective_success = None
    elif blocking_markers:
        status = "fetched_sparse"
        effective_success = None
    else:
        quality_success = True
        effective_success = succeeded_at or attempted_at
        current_time = now or datetime.now(timezone.utc)
        if "too_short" in markers:
            status = "source_short_complete"
        elif current_time - effective_success > stale_after:
            status = "stale"
        else:
            status = "fetched_complete"

    if "synthetic_fallback" in markers:
        content_state: DescriptionContentState = "synthetic"
    elif not text:
        content_state = "missing"
    elif status == "stale":
        content_state = "stale"
    else:
        content_state = "source"

    return DescriptionObservation(
        status=status,
        content_state=content_state,
        length=length,
        sha256=digest,
        extractor_version=extractor_version,
        attempted_at=attempted_at,
        succeeded_at=effective_success,
        extraction_succeeded=quality_success,
        markers=markers,
        http_status=http_status,
        error=error,
        full_text_evidence=bool(full_text_evidence and text),
    )


def _normalized_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _content_digest(value: str) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _parse_datetime(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def select_description(
    existing: str | None,
    candidate: str | None,
    *,
    candidate_observation: DescriptionObservation,
    existing_succeeded_at: datetime | str | None = None,
    existing_full_text_evidence: bool = False,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(days=7),
    minimum_length: int = 80,
) -> DescriptionSelection:
    """Select content without allowing a weak attempt to erase useful source text.

    The attempt evidence is kept separately by callers. This function only decides
    which user-facing text survives. A source candidate replaces existing content
    when the old value is absent/synthetic, or when the new value is strictly
    longer. Empty, boilerplate and visibly truncated candidates never win.
    """
    current_time = now or datetime.now(timezone.utc)
    existing_text = _normalized_text(existing)
    candidate_text = _normalized_text(candidate)
    existing_success = _parse_datetime(existing_succeeded_at)
    existing_observation = assess_description(
        existing_text,
        extractor_version="existing",
        attempted_at=existing_success or current_time if existing_text else None,
        succeeded_at=existing_success,
        now=current_time,
        stale_after=stale_after,
        minimum_length=minimum_length,
        full_text_evidence=existing_full_text_evidence,
    )
    candidate_is_source = candidate_observation.content_state in {"source", "stale"}
    candidate_is_safe = candidate_is_source and not {
        "empty",
        "synthetic_fallback",
        "expand_prompt",
        "truncated_ellipsis",
    }.intersection(candidate_observation.markers)
    existing_is_weak = existing_observation.content_state in {"missing", "synthetic"}
    existing_is_visibly_truncated = bool(
        {"expand_prompt", "truncated_ellipsis"}.intersection(existing_observation.markers)
    )
    candidate_is_less_verified = (
        existing_observation.full_text_evidence
        and not candidate_observation.full_text_evidence
    )
    should_replace = candidate_is_safe and not candidate_is_less_verified and (
        existing_is_weak or len(candidate_text) > len(existing_text))
    if (
        candidate_is_safe and existing_is_visibly_truncated
        and candidate_observation.full_text_evidence
    ):
        should_replace = True

    if should_replace:
        chosen = candidate_text
        state = candidate_observation.content_state
        kept_existing = False
        reason = "source_candidate_replaces_weak_existing" if existing_is_weak else "longer_source_candidate"
        if existing_is_visibly_truncated and candidate_observation.full_text_evidence:
            reason = "verified_source_replaces_truncated_existing"
        selected_full_text_evidence = candidate_observation.full_text_evidence
    else:
        chosen = existing_text
        state = existing_observation.content_state
        kept_existing = True
        if not candidate_is_safe:
            reason = "candidate_not_source_quality"
        elif len(candidate_text) <= len(existing_text):
            reason = "candidate_not_longer"
        else:
            reason = "existing_preserved"
        selected_full_text_evidence = existing_observation.full_text_evidence

    return DescriptionSelection(
        text=chosen,
        content_state=state,
        length=len(chosen),
        sha256=_content_digest(chosen),
        kept_existing=kept_existing,
        reason=reason,
        full_text_evidence=selected_full_text_evidence,
    )

def audit_descriptions(
    rows: Iterable[dict[str, Any]],
    *,
    sources: tuple[str, ...] | None = None,
    minimum_length: int = 80,
) -> dict[str, Any]:
    """Return content and detail-attempt evidence, for all portals by default."""
    target_sources = (
        {source.strip().lower() for source in sources if source.strip()}
        if sources is not None
        else None
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        source = str(row.get("source_site") or row.get("source") or "").strip().lower()
        if target_sources is not None and source not in target_sources:
            continue
        text = _normalized_text(
            row.get("description_full") or row.get("description") or ""
        )
        content_length = int(row.get("description_length") or len(text))
        markers = _quality_markers(text, minimum_length=minimum_length)
        stored_content_state = row.get("description_content_state")
        if stored_content_state:
            content_state = str(stored_content_state).strip().lower()
        elif "synthetic_fallback" in markers:
            content_state = "synthetic"
        else:
            content_state = "missing" if not text else "source"
        attempted_at = row.get("description_attempted_at")
        attempted = bool(attempted_at)
        full_text_evidence = bool(row.get("description_full_text_evidence"))
        attempt_status = str(row.get("description_attempt_status") or (
            "not_attempted" if not attempted else "unknown"
        )).strip().lower()
        source_complete = (
            content_state in {"source", "stale"}
            and content_length >= minimum_length
            and not ({"synthetic_fallback", "expand_prompt", "truncated_ellipsis"} & set(markers))
        )
        if source_complete:
            explanation = "source_complete"
        elif (
            attempted
            and attempt_status == "source_short_complete"
            and bool(text)
            and content_state in {"source", "stale"}
            and not ({"synthetic_fallback", "expand_prompt", "truncated_ellipsis"} & set(markers))
            and full_text_evidence
        ):
            explanation = attempt_status
        elif attempted and attempt_status in {"blocked", "error"}:
            explanation = attempt_status
        elif attempted and attempt_status == "fetched_sparse":
            if content_state == "synthetic" or "synthetic_fallback" in markers:
                explanation = "synthetic_response"
            elif int(row.get("description_attempt_length") or 0) == 0:
                explanation = "empty_response"
            else:
                explanation = "sparse_response"
        elif not attempted:
            explanation = "unattempted"
        else:
            explanation = "unexplained"
        item = {
            "source": source,
            "source_id": str(row.get("source_id") or row.get("id") or ""),
            "content_state": content_state,
            "content_length": content_length,
            "content_sha256": row.get("description_sha256") or _content_digest(text),
            "short": content_length < minimum_length,
            "source_complete": source_complete,
            "attempted_at": attempted_at,
            "attempt_status": attempt_status,
            "attempt_length": int(row.get("description_attempt_length") or 0),
            "attempt_sha256": row.get("description_attempt_sha256"),
            "http_status": row.get("http_status"),
            "error": row.get("description_attempt_error"),
            "extractor_version": row.get("description_extractor_version"),
            "explanation": explanation,
            "unexplained": explanation in {"unattempted", "unexplained"},
            "full_text_evidence": full_text_evidence,
        }
        items.append(item)

    items.sort(key=lambda item: (item["source"], item["source_id"]))
    attempted = sum(bool(item["attempted_at"]) for item in items)
    audited_sources = (
        sorted(target_sources)
        if target_sources is not None
        else sorted({item["source"] for item in items})
    )
    return {
        "summary": {
            "targeted": len(items),
            "short": sum(bool(item["short"]) for item in items),
            "complete_source": sum(bool(item["source_complete"]) for item in items),
            "source_short_complete": sum(
                item["attempt_status"] == "source_short_complete" for item in items
            ),
            "synthetic": sum(item["content_state"] == "synthetic" for item in items),
            "blocked": sum(item["attempt_status"] == "blocked" for item in items),
            "errors": sum(item["attempt_status"] == "error" for item in items),
            "attempted": attempted,
            "unattempted": sum(item["explanation"] == "unattempted" for item in items),
            "unexplained": sum(bool(item["unexplained"]) for item in items),
        },
        "sources": audited_sources,
        "minimum_length": minimum_length,
        "items": items,
    }


def load_description_audit_rows(
    db_path: Path,
    *,
    sources: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Read active canonical rows and optional detail-attempt evidence."""
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "rental_listings" not in tables:
            raise RuntimeError("rental_listings table missing")
        detail_columns = (
            {row[1] for row in con.execute("PRAGMA table_info(listing_detail)")}
            if "listing_detail" in tables
            else set()
        )
        evidence_columns = (
            "description_content_state",
            "description_length",
            "description_sha256",
            "description_attempt_status",
            "description_attempted_at",
            "description_full_text_evidence",
            "description_attempt_length",
            "description_attempt_sha256",
            "description_attempt_error",
            "description_extractor_version",
            "http_status",
        )
        if detail_columns:
            description_expr = (
                "COALESCE(NULLIF(TRIM(d.description_full),''),r.description)"
                if "description_full" in detail_columns
                else "r.description"
            )
            evidence = [
                f"d.{name} AS {name}" if name in detail_columns else f"NULL AS {name}"
                for name in evidence_columns
            ]
            join = (
                "LEFT JOIN listing_detail d ON d.source_site=r.source_site "
                "AND d.source_id=r.source_id"
            )
        else:
            description_expr = "r.description"
            evidence = [f"NULL AS {name}" for name in evidence_columns]
            join = ""
        where = "WHERE COALESCE(r.is_active,1)=1"
        params: tuple[str, ...] = ()
        if sources:
            placeholders = ",".join("?" for _ in sources)
            where += f" AND lower(r.source_site) IN ({placeholders})"
            params = tuple(source.lower() for source in sources)
        query = (
            "SELECT r.source_site,r.source_id,"
            f"{description_expr} AS description_full,{','.join(evidence)} "
            f"FROM rental_listings r {join} "
            f"{where} ORDER BY r.source_site,r.source_id"
        )
        return [dict(row) for row in con.execute(query, params)]
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit description evidence for active listings")
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--sources",
        default="",
        help="optional comma-separated portal subset; default audits every portal",
    )
    parser.add_argument("--minimum-length", type=int, default=80)
    parser.add_argument("--output")
    args = parser.parse_args()
    sources = tuple(source.strip() for source in args.sources.split(",") if source.strip()) or None
    rows = load_description_audit_rows(Path(args.db), sources=sources)
    report = audit_descriptions(rows, sources=sources, minimum_length=args.minimum_length)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if report["summary"]["unexplained"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
