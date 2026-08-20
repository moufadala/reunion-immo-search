"""Strict, machine-readable accounting for the real-estate pipeline.

The module does not scrape or mutate data.  It validates the evidence emitted by
adapters and by the publication pipeline, and rejects unexplained reductions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


SOURCE_STATUSES = {"complete", "partial", "failed"}
MANDATORY_FIELD_COUNTERS = {
    "missing_title",
    "missing_rent",
    "missing_surface",
    "missing_commune",
    "missing_description",
    "missing_photo",
}
HARD_VISIBLE_FIELD_COUNTERS = {"missing_description", "missing_photo"}


class ManifestValidationError(ValueError):
    """Raised when a source manifest cannot account for its own counters."""


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ManifestValidationError(f"{name} must be an integer, not bool")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError(f"{name} must be an integer") from exc
    if result < 0:
        raise ManifestValidationError(f"{name} must be non-negative")
    return result


@dataclass(frozen=True, slots=True)
class SourceRunManifest:
    run_id: str
    source: str
    status: str
    attempted: bool
    pages_attempted: int
    pages_succeeded: int
    fetched_items: int
    parsed_items: int
    unique_ids: int
    normalized_items: int
    rejected_items: int
    inserted: int
    updated: int
    unchanged: int
    withdrawn: int
    reappeared: int
    expected_count: int | None
    dataset_id: str | None
    retries: int
    truncation_signals: tuple[str, ...]
    error: str | None
    rejected_items_by_reason: tuple[tuple[str, int], ...] = ()
    unparsed_items_by_reason: tuple[tuple[str, int], ...] = ()
    pre_unique_rejections_by_reason: tuple[tuple[str, int], ...] = ()
    seen_ids: tuple[str, ...] = ()
    terminal_reason: str | None = None
    snapshot_proof: str | None = None
    previous_count: int | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SourceRunManifest":
        required = {
            "run_id", "source", "status", "attempted", "pages_attempted",
            "pages_succeeded", "fetched_items", "parsed_items", "unique_ids",
            "normalized_items", "rejected_items", "inserted", "updated",
            "unchanged", "withdrawn", "reappeared", "expected_count",
            "dataset_id", "retries", "truncation_signals", "error", "seen_ids",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise ManifestValidationError(f"missing manifest counters: {', '.join(missing)}")
        status = str(payload["status"]).strip().lower()
        if status not in SOURCE_STATUSES:
            raise ManifestValidationError(f"invalid source status: {status!r}")
        run_id = str(payload["run_id"]).strip()
        source = str(payload["source"]).strip()
        if not run_id or not source:
            raise ManifestValidationError("run_id and source must be non-empty")
        expected = payload.get("expected_count")
        if not isinstance(payload["attempted"], bool):
            raise ManifestValidationError("attempted must be a boolean")
        previous = payload.get("previous_count")
        raw_rejection_reasons = payload.get("rejected_items_by_reason")
        if raw_rejection_reasons is None:
            rejection_reasons: tuple[tuple[str, int], ...] = ()
        elif not isinstance(raw_rejection_reasons, Mapping):
            raise ManifestValidationError("rejected_items_by_reason must be an object")
        else:
            parsed_reasons: dict[str, int] = {}
            for raw_reason, raw_count in raw_rejection_reasons.items():
                reason = str(raw_reason).strip()
                if not reason:
                    raise ManifestValidationError(
                        "rejected_items_by_reason keys must be non-empty"
                    )
                count = _integer(
                    raw_count, f"rejected_items_by_reason.{reason}"
                )
                if count == 0:
                    raise ManifestValidationError(
                        "rejected_items_by_reason counts must be positive"
                    )
                if reason in parsed_reasons:
                    raise ManifestValidationError(
                        f"duplicate rejected_items_by_reason key: {reason}"
                    )
                parsed_reasons[reason] = count
            rejection_reasons = tuple(sorted(parsed_reasons.items()))
        transition_reason_sets: dict[str, tuple[tuple[str, int], ...]] = {}
        for field_name in (
            "unparsed_items_by_reason",
            "pre_unique_rejections_by_reason",
        ):
            raw_reasons = payload.get(field_name)
            if raw_reasons is None:
                transition_reason_sets[field_name] = ()
                continue
            if not isinstance(raw_reasons, Mapping):
                raise ManifestValidationError(f"{field_name} must be an object")
            parsed_transition_reasons: dict[str, int] = {}
            for raw_reason, raw_count in raw_reasons.items():
                reason = str(raw_reason).strip()
                if not reason:
                    raise ManifestValidationError(f"{field_name} keys must be non-empty")
                count = _integer(raw_count, f"{field_name}.{reason}")
                if count == 0:
                    raise ManifestValidationError(f"{field_name} counts must be positive")
                parsed_transition_reasons[reason] = count
            transition_reason_sets[field_name] = tuple(
                sorted(parsed_transition_reasons.items())
            )
        raw_seen_ids = payload.get("seen_ids")
        if not isinstance(raw_seen_ids, (list, tuple)):
            raise ManifestValidationError("seen_ids must be a list")
        seen_ids = tuple(str(value).strip() for value in raw_seen_ids)
        if any(not value for value in seen_ids):
            raise ManifestValidationError("seen_ids entries must be non-empty")
        if len(set(seen_ids)) != len(seen_ids):
            raise ManifestValidationError("seen_ids must be distinct")
        item = cls(
            run_id=run_id,
            source=source,
            status=status,
            attempted=payload["attempted"],
            pages_attempted=_integer(payload["pages_attempted"], "pages_attempted"),
            pages_succeeded=_integer(payload["pages_succeeded"], "pages_succeeded"),
            fetched_items=_integer(payload["fetched_items"], "fetched_items"),
            parsed_items=_integer(payload["parsed_items"], "parsed_items"),
            unique_ids=_integer(payload["unique_ids"], "unique_ids"),
            normalized_items=_integer(payload["normalized_items"], "normalized_items"),
            rejected_items=_integer(payload["rejected_items"], "rejected_items"),
            inserted=_integer(payload["inserted"], "inserted"),
            updated=_integer(payload["updated"], "updated"),
            unchanged=_integer(payload["unchanged"], "unchanged"),
            withdrawn=_integer(payload["withdrawn"], "withdrawn"),
            reappeared=_integer(payload["reappeared"], "reappeared"),
            expected_count=None if expected is None else _integer(expected, "expected_count"),
            dataset_id=str(payload["dataset_id"]).strip() if payload.get("dataset_id") else None,
            retries=_integer(payload["retries"], "retries"),
            truncation_signals=tuple(str(x) for x in (payload.get("truncation_signals") or ())),
            error=str(payload["error"]).strip() if payload.get("error") else None,
            rejected_items_by_reason=rejection_reasons,
            unparsed_items_by_reason=transition_reason_sets[
                "unparsed_items_by_reason"
            ],
            pre_unique_rejections_by_reason=transition_reason_sets["pre_unique_rejections_by_reason"],
            seen_ids=seen_ids,
            terminal_reason=str(payload["terminal_reason"]).strip()
            if payload.get("terminal_reason") else None,
            snapshot_proof=str(payload["snapshot_proof"]).strip()
            if payload.get("snapshot_proof") else None,
            previous_count=None if previous is None else _integer(previous, "previous_count"),
        )
        item.validate()
        return item

    @property
    def authoritative_for_withdrawals(self) -> bool:
        return self.status == "complete"

    @property
    def comparison_count(self) -> int | None:
        return self.expected_count if self.expected_count is not None else self.previous_count

    @property
    def coverage_ratio(self) -> float | None:
        baseline = self.comparison_count
        if baseline is None:
            return None
        if baseline == 0:
            return 1.0 if self.unique_ids == 0 else None
        return self.unique_ids / baseline

    def validate(self) -> None:
        if self.pages_succeeded > self.pages_attempted:
            raise ManifestValidationError("pages_succeeded cannot exceed pages_attempted")
        if self.parsed_items > self.fetched_items:
            raise ManifestValidationError("parsed_items cannot exceed fetched_items")
        if self.unique_ids > self.parsed_items:
            raise ManifestValidationError("unique_ids cannot exceed parsed_items")
        unparsed_reason_total = sum(count for _, count in self.unparsed_items_by_reason)
        if unparsed_reason_total != self.fetched_items - self.parsed_items:
            raise ManifestValidationError(
                "unparsed_items_by_reason must account for fetched_items - parsed_items"
            )
        pre_unique_reason_total = sum(count for _, count in self.pre_unique_rejections_by_reason)
        if pre_unique_reason_total != self.parsed_items - self.unique_ids:
            raise ManifestValidationError(
                "pre_unique_rejections_by_reason must account for parsed_items - unique_ids"
            )
        if self.normalized_items + self.rejected_items != self.unique_ids:
            raise ManifestValidationError(
                "normalized_items + rejected_items must equal unique_ids"
            )
        rejection_reason_total = sum(count for _, count in self.rejected_items_by_reason)
        if rejection_reason_total != self.rejected_items:
            raise ManifestValidationError(
                "rejected_items_by_reason must account for every rejected item"
            )
        if self.inserted + self.updated + self.unchanged != self.normalized_items:
            raise ManifestValidationError(
                "inserted + updated + unchanged must equal normalized_items"
            )
        if len(self.seen_ids) != self.normalized_items:
            raise ManifestValidationError("seen_ids must match normalized_items")
        if not self.attempted and any(
            (self.pages_attempted, self.fetched_items, self.normalized_items)
        ):
            raise ManifestValidationError("unattempted source cannot report collected items")
        if self.status == "complete":
            if not self.attempted:
                raise ManifestValidationError("complete source must be attempted")
            if self.pages_attempted == 0:
                raise ManifestValidationError("complete source requires page evidence")
            if (
                self.expected_count is None and not self.dataset_id
                and not self.terminal_reason and not self.snapshot_proof
            ):
                raise ManifestValidationError("complete source requires terminal evidence")
            if self.error:
                raise ManifestValidationError("complete source cannot carry an error")
            if self.truncation_signals:
                raise ManifestValidationError("complete source cannot carry truncation signals")
            if self.pages_succeeded != self.pages_attempted:
                raise ManifestValidationError("complete source must succeed every attempted page")
            if self.expected_count is not None and self.unique_ids != self.expected_count:
                raise ManifestValidationError("complete source must match exposed expected_count")
        elif self.status == "partial":
            if not self.truncation_signals and not self.error:
                raise ManifestValidationError("partial source requires an explicit signal or error")
        elif not self.error:
            raise ManifestValidationError("failed source requires an error")
        if not self.authoritative_for_withdrawals and self.withdrawn:
            raise ManifestValidationError("partial/failed source cannot withdraw listings")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["truncation_signals"] = list(self.truncation_signals)
        payload["rejected_items_by_reason"] = dict(self.rejected_items_by_reason)
        payload["unparsed_items_by_reason"] = dict(self.unparsed_items_by_reason)
        payload["pre_unique_rejections_by_reason"] = dict(self.pre_unique_rejections_by_reason)
        payload["seen_ids"] = list(self.seen_ids)
        payload["authoritative_for_withdrawals"] = self.authoritative_for_withdrawals
        return payload


def evaluate_source_manifests(
    manifests: Iterable[SourceRunManifest | Mapping[str, Any]],
    *,
    critical_sources: set[str] | frozenset[str],
    coverage_floors: Mapping[str, float] | None = None,
    consecutive_partial_runs: Mapping[str, int] | None = None,
    noncritical_partial_escalates_after: int = 2,
) -> dict[str, Any]:
    floors = coverage_floors or {}
    streaks = consecutive_partial_runs or {}
    raw_manifests = list(manifests)
    parsed: list[SourceRunManifest] = []
    errors: list[str] = []
    for raw in raw_manifests:
        try:
            parsed.append(raw if isinstance(raw, SourceRunManifest) else SourceRunManifest.from_dict(raw))
        except ManifestValidationError as exc:
            source = str(raw.get("source", "unknown")) if isinstance(raw, Mapping) else raw.source
            errors.append(f"{source}: invalid manifest: {exc}")
    duplicate_sources = sorted({m.source for m in parsed if sum(x.source == m.source for x in parsed) > 1})
    errors.extend(f"{source}: duplicate source manifest" for source in duplicate_sources)

    blocking: set[str] = set()
    warnings: set[str] = set()
    source_rows: list[dict[str, Any]] = []
    for item in sorted(parsed, key=lambda x: x.source):
        floor = float(floors.get(item.source, 0.9))
        ratio = item.coverage_ratio
        reason = None
        if item.status == "failed":
            reason = item.error or "failed"
            (blocking if item.source in critical_sources else warnings).add(item.source)
        elif item.status == "partial":
            warnings.add(item.source)
            if item.source in critical_sources:
                blocking.add(item.source)
                reason = "critical source did not prove a complete snapshot"
            elif item.source not in critical_sources and int(streaks.get(item.source, 1)) >= noncritical_partial_escalates_after:
                blocking.add(item.source)
                reason = "noncritical source partial on consecutive runs"
        source_rows.append({
            "source": item.source,
            "status": item.status,
            "coverage_ratio": ratio,
            "coverage_floor": floor,
            "authoritative_for_withdrawals": item.authoritative_for_withdrawals,
            "blocking_reason": reason,
        })

    if errors:
        blocking.update(
            str(raw.get("source", "unknown"))
            for raw in raw_manifests
            if isinstance(raw, Mapping)
        )
    return {
        "ok": not blocking and not errors,
        "blocking_sources": sorted(blocking),
        "warning_sources": sorted(warnings),
        "errors": errors,
        "sources": source_rows,
    }


def _counter(mapping: Mapping[str, Any], key: str, errors: list[str], section: str) -> int:
    if key not in mapping:
        errors.append(f"{section}: missing mandatory counter {key}")
        return 0
    try:
        return _integer(mapping[key], f"{section}.{key}")
    except ManifestValidationError as exc:
        errors.append(str(exc))
        return 0


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return sorted({value for value in values if value in seen or seen.add(value)})


def reconcile_pipeline(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one prepared end-to-end reconciliation payload."""
    errors: list[str] = []
    warnings: list[str] = []
    run_id = str(report.get("run_id") or "").strip()
    if not run_id:
        errors.append("missing run_id")

    manifests: list[SourceRunManifest] = []
    for raw in report.get("sources") or []:
        try:
            item = SourceRunManifest.from_dict(raw)
            manifests.append(item)
            if item.run_id != run_id:
                errors.append(f"{item.source}: manifest run_id differs from report run_id")
        except (ManifestValidationError, TypeError) as exc:
            errors.append(f"invalid source manifest: {exc}")
    if not manifests:
        errors.append("no source manifests")

    database = report.get("database") if isinstance(report.get("database"), Mapping) else {}
    db = {key: _counter(database, key, errors, "database") for key in (
        "total", "active", "inactive", "new", "disappeared", "reappeared"
    )}
    if db["active"] + db["inactive"] != db["total"]:
        errors.append("database: active + inactive must equal total")
    if manifests and sum(m.inserted for m in manifests) != db["new"]:
        errors.append("database: new must equal sum(source.inserted)")
    if manifests and sum(m.withdrawn for m in manifests) != db["disappeared"]:
        errors.append("database: disappeared must equal sum(source.withdrawn)")
    if manifests and sum(m.reappeared for m in manifests) != db["reappeared"]:
        errors.append("database: reappeared must equal sum(source.reappeared)")

    product = report.get("product") if isinstance(report.get("product"), Mapping) else {}
    p = {key: _counter(product, key, errors, "product") for key in (
        "active_input", "eligible", "dedup_hidden", "visible"
    )}
    exclusions = product.get("policy_exclusions")
    if not isinstance(exclusions, Mapping):
        errors.append("product: missing mandatory counter map policy_exclusions")
        exclusions = {}
    exclusion_total = 0
    for reason, value in exclusions.items():
        try:
            exclusion_total += _integer(value, f"product.policy_exclusions.{reason}")
        except ManifestValidationError as exc:
            errors.append(str(exc))
    if p["active_input"] - exclusion_total != p["eligible"]:
        errors.append("product: active_input - policy_exclusions must equal eligible")
    if p["eligible"] - p["dedup_hidden"] != p["visible"]:
        errors.append("product: eligible - dedup_hidden must equal visible")

    eligible_ids = [str(x) for x in (product.get("eligible_ids") or [])]
    visible_ids = [str(x) for x in (product.get("visible_ids") or [])]
    also_on_ids = [str(x) for x in (product.get("also_on_ids") or [])]
    for name, values, expected in (
        ("eligible", eligible_ids, p["eligible"]),
        ("visible", visible_ids, p["visible"]),
        ("also_on", also_on_ids, p["dedup_hidden"]),
    ):
        if len(values) != expected:
            errors.append(f"product: {name}_ids count differs from {name} counter")
        duplicates = _duplicates(values)
        if duplicates:
            errors.append(f"product: duplicate {name} identity: {', '.join(duplicates)}")
    if set(visible_ids) & set(also_on_ids):
        errors.append("product: visible_ids and also_on_ids must be disjoint")
    if set(eligible_ids) != set(visible_ids) | set(also_on_ids):
        errors.append("product: identity representation mismatch")

    fields = report.get("fields") if isinstance(report.get("fields"), Mapping) else {}
    explanations = report.get("field_explanations") if isinstance(report.get("field_explanations"), Mapping) else {}
    missing_field_counters = sorted(MANDATORY_FIELD_COUNTERS - set(fields))
    errors.extend(f"fields: missing mandatory counter {key}" for key in missing_field_counters)
    for key in sorted(MANDATORY_FIELD_COUNTERS & set(fields)):
        try:
            missing_count = _integer(fields[key], f"fields.{key}")
            explained = _integer(explanations.get(key, 0), f"field_explanations.{key}")
        except ManifestValidationError as exc:
            errors.append(str(exc))
            continue
        if key in HARD_VISIBLE_FIELD_COUNTERS and missing_count:
            errors.append(f"fields: visible {key}={missing_count}")
            continue
        if explained > missing_count:
            errors.append(f"fields: explanation count exceeds {key}")
        elif missing_count != explained:
            errors.append(f"fields: unexplained {key}={missing_count - explained}")

    return {
        "ok": not errors,
        "run_id": run_id or None,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "sources": len(manifests),
            "active_input": p["active_input"],
            "eligible": p["eligible"],
            "visible": p["visible"],
            "represented_identities": len(set(visible_ids) | set(also_on_ids)),
        },
    }
