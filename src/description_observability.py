"""Deterministic quality observations for scraped listing descriptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import unicodedata
from typing import Literal


DescriptionStatus = Literal[
    "not_attempted",
    "fetched_complete",
    "fetched_sparse",
    "blocked",
    "error",
    "stale",
]


@dataclass(frozen=True)
class DescriptionObservation:
    status: DescriptionStatus
    length: int
    sha256: str | None
    extractor_version: str
    attempted_at: datetime | None
    succeeded_at: datetime | None
    extraction_succeeded: bool
    markers: tuple[str, ...]
    http_status: int | None
    error: str | None


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
) -> DescriptionObservation:
    """Classify one extraction attempt while retaining auditable evidence.

    A transport success is deliberately not an extraction success: blank, short,
    synthetic, or visibly truncated text remains ``fetched_sparse``.
    """
    text = " ".join((description or "").split())
    length = len(text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
    markers = _quality_markers(text, minimum_length=minimum_length)

    status: DescriptionStatus
    quality_success = False
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
    elif markers:
        status = "fetched_sparse"
        effective_success = None
    else:
        quality_success = True
        effective_success = succeeded_at or attempted_at
        current_time = now or datetime.now(timezone.utc)
        if current_time - effective_success > stale_after:
            status = "stale"
        else:
            status = "fetched_complete"

    return DescriptionObservation(
        status=status,
        length=length,
        sha256=digest,
        extractor_version=extractor_version,
        attempted_at=attempted_at,
        succeeded_at=effective_success,
        extraction_succeeded=quality_success,
        markers=markers,
        http_status=http_status,
        error=error,
    )
