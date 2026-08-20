"""Build an independent, blocking reconciliation proof for one pipeline run.

The public feed declares the product accounting.  This module checks that claim
against the real feed identities and against SQLite before/after inventories,
then delegates the canonical equations to :mod:`src.pipeline_reconciliation`.
It never mutates either database.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.pipeline_reconciliation import evaluate_source_manifests, reconcile_pipeline
from src.publication_policy import evaluate_publication


def _read_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be an object")
    return payload


def discover_before_db(manifests_path: Path) -> Path:
    """Return the unique pre-collection DB snapshot for either manifest layout."""
    candidate_dirs = [
        manifests_path.parent / "backups",
        manifests_path.parent / "realestate_watch" / "backups",
    ]
    by_resolved_path = {
        path.resolve(): path
        for directory in candidate_dirs
        for path in directory.glob("*.bak.*")
        if path.is_file()
    }
    candidates = sorted(by_resolved_path.values(), key=lambda path: str(path))
    if len(candidates) != 1:
        searched = ", ".join(str(path) for path in candidate_dirs)
        raise ValueError(
            f"expected exactly one pre-run DB backup, found {len(candidates)}; "
            f"searched: {searched}"
        )
    return candidates[0]

def _db_inventory(path: Path) -> dict[str, bool]:
    if not path.is_file():
        raise FileNotFoundError(f"database not found: {path}")
    with closing(sqlite3.connect(str(path))) as con:
        columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(rental_listings)")
        }
        required = {"source_site", "source_id", "is_active"}
        missing = sorted(required - columns)
        if missing:
            raise ValueError(
                f"database {path} rental_listings missing columns: {', '.join(missing)}"
            )
        rows = con.execute(
            "SELECT source_site, source_id, is_active FROM rental_listings"
        ).fetchall()

    inventory: dict[str, bool] = {}
    for source, source_id, raw_active in rows:
        source_text = str(source or "").strip()
        id_text = str(source_id or "").strip()
        if not source_text or not id_text:
            raise ValueError(f"database {path} contains an empty listing identity")
        identity = f"{source_text}:{id_text}"
        if identity in inventory:
            raise ValueError(f"database {path} contains duplicate identity {identity}")
        if raw_active not in (None, 0, 1):
            raise ValueError(
                f"database {path} has invalid is_active={raw_active!r} for {identity}"
            )
        inventory[identity] = raw_active is None or int(raw_active) == 1
    return inventory


def _optional_rows(
    con: sqlite3.Connection,
    table: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    queries = {
        "listing_product_enrichment": "SELECT * FROM listing_product_enrichment",
        "listing_detail": "SELECT * FROM listing_detail",
    }
    if table not in queries:
        raise ValueError(f"unsupported optional SQLite table: {table}")
    try:
        rows = con.execute(queries[table]).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return {}
        raise ValueError(f"unable to read optional SQLite table {table}") from exc
    result = {}
    for row in rows:
        item = dict(row)
        key = (
            str(item.get("source_site") or "").strip(),
            str(item.get("source_id") or "").strip(),
        )
        if all(key):
            result[key] = item
    return result


def _preferred_normalized(value: Any, fallback: Any) -> Any:
    text = str(value or "").strip()
    folded = text.casefold().replace("-", " ").replace("_", " ")
    if folded in {
        "",
        "zone non precisee",
        "non précisée",
        "non precisee",
        "non precise",
        "region non precisee",
        "non renseigne",
        "autre",
        "inconnu",
        "na",
    }:
        return fallback
    return value


def _db_policy_inputs(path: Path) -> dict[str, dict[str, Any]]:
    with closing(sqlite3.connect(str(path))) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(row) for row in con.execute("SELECT * FROM rental_listings")]
        enrichment = _optional_rows(con, "listing_product_enrichment")
        details = _optional_rows(con, "listing_detail")

    result = {}
    for row in rows:
        source = str(row.get("source_site") or "").strip()
        source_id = str(row.get("source_id") or "").strip()
        identity = f"{source}:{source_id}"
        enriched = enrichment.get((source, source_id), {})
        detail = details.get((source, source_id), {})
        normalized_city = _preferred_normalized(
            enriched.get("city_normalized"), row.get("city")
        )
        normalized_zone = _preferred_normalized(
            enriched.get("zone_normalized"), row.get("district")
        )
        residential = enriched.get("is_residential")
        result[identity] = {
            "surface_m2": row.get("surface_m2"),
            "rent_eur": row.get("rent_eur"),
            "commune": normalized_city,
            "quartier": normalized_zone,
            "property_type": enriched.get("property_type_normalized")
            or row.get("property_type"),
            "title": row.get("title"),
            "description": detail.get("description_full") or row.get("description"),
            "residential": bool(residential) if residential is not None else None,
        }
    return result


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _database_report(
    before: Mapping[str, bool], current: Mapping[str, bool]
) -> tuple[dict[str, int], list[str]]:
    before_ids = set(before)
    current_ids = set(current)
    common = before_ids & current_ids
    report = {
        "total": len(current_ids),
        "active": sum(1 for active in current.values() if active),
        "inactive": sum(1 for active in current.values() if not active),
        "new": len(current_ids - before_ids),
        "disappeared": sum(1 for identity in common if before[identity] and not current[identity]),
        "reappeared": sum(1 for identity in common if not before[identity] and current[identity]),
    }
    return report, sorted(before_ids - current_ids)


def build_runtime_reconciliation(
    *,
    feed_path: Path,
    manifests_path: Path,
    db_path: Path,
    before_db_path: Path | None = None,
    expected_sources: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Build and validate the exact run proof used as a publication gate."""
    feed_path = Path(feed_path)
    manifests_path = Path(manifests_path)
    db_path = Path(db_path)
    before_db_path = Path(before_db_path) if before_db_path else discover_before_db(manifests_path)

    feed = _read_object(feed_path, "feed")
    manifest_bundle = _read_object(manifests_path, "source manifests")
    run_id = str(manifest_bundle.get("run_id") or "").strip()
    raw_sources = manifest_bundle.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError("source manifests.sources must be a list")

    product_raw = (feed.get("meta") or {}).get("reconciliation_product")
    if not isinstance(product_raw, Mapping):
        raise ValueError("feed meta.reconciliation_product must be an object")
    product = {
        key: product_raw.get(key)
        for key in (
            "active_input",
            "policy_exclusions",
            "eligible",
            "dedup_hidden",
            "visible",
            "eligible_ids",
            "visible_ids",
            "also_on_ids",
            "excluded_ids",
            "unexplained_eligible_ids",
            "unexpected_also_on_ids",
            "invalid_also_on_links",
        )
    }
    fields = product_raw.get("fields")
    explanations = product_raw.get("field_explanations")
    explanation_reasons = product_raw.get("field_explanation_reasons")

    before = _db_inventory(before_db_path)
    current = _db_inventory(db_path)
    database, deleted_ids = _database_report(before, current)
    report = {
        "run_id": run_id,
        "sources": raw_sources,
        "database": database,
        "product": product,
        "fields": fields,
        "field_explanations": explanations,
    }
    core = reconcile_pipeline(report)

    errors = list(core.get("errors") or [])
    warnings = list(core.get("warnings") or [])
    expected = {str(source).strip().lower() for source in expected_sources if str(source).strip()}
    actual_sources = {
        str(item.get("source") or "").strip().lower()
        for item in raw_sources
        if isinstance(item, Mapping) and str(item.get("source") or "").strip()
    }
    missing_sources = sorted(expected - actual_sources)
    unexpected_sources = sorted(actual_sources - expected)
    if missing_sources:
        errors.append(f"missing source manifests: {', '.join(missing_sources)}")
    if unexpected_sources:
        errors.append(f"unexpected source manifests: {', '.join(unexpected_sources)}")

    source_gate = evaluate_source_manifests(raw_sources, critical_sources=expected)
    if not source_gate["ok"]:
        errors.extend(
            f"source manifest gate blocked: {source}"
            for source in source_gate.get("blocking_sources") or []
        )
        errors.extend(
            f"source manifest gate invalid: {error}"
            for error in source_gate.get("errors") or []
        )

    if deleted_ids:
        sample = ", ".join(deleted_ids[:5])
        errors.append(
            f"database identities deleted between snapshots: {len(deleted_ids)}"
            + (f" ({sample})" if sample else "")
        )

    if product.get("active_input") != database["active"]:
        errors.append(
            f"product.active_input={product.get('active_input')} differs from "
            f"database.active={database['active']}"
        )

    listings = feed.get("listings")
    if not isinstance(listings, list):
        errors.append("feed listings must be a list")
        listings = []
    feed_ids = [
        str(item.get("id") or "").strip()
        for item in listings
        if isinstance(item, Mapping)
    ]
    if any(not identity for identity in feed_ids) or len(feed_ids) != len(listings):
        errors.append("feed contains a listing without a usable identity")
    duplicate_feed_ids = _duplicates(feed_ids)
    if duplicate_feed_ids:
        errors.append(f"feed duplicate identities: {', '.join(duplicate_feed_ids)}")
    claimed_visible_ids = [str(value) for value in (product.get("visible_ids") or [])]
    if set(feed_ids) != set(claimed_visible_ids) or len(feed_ids) != len(claimed_visible_ids):
        errors.append("feed visible identities differ from product.visible_ids")
    actual_linked_ids: set[str] = set()
    invalid_link_count = 0
    missing_description_ids: list[str] = []
    missing_photo_ids: list[str] = []
    for item in listings:
        if not isinstance(item, Mapping):
            continue
        identity = str(item.get("id") or "").strip() or "<missing-id>"
        if not str(item.get("description") or "").strip():
            missing_description_ids.append(identity)
        raw_images = item.get("images")
        gallery = raw_images if isinstance(raw_images, list) else []
        has_photo = bool(str(item.get("image") or "").strip()) or any(
            bool(str(value or "").strip()) for value in gallery
        )
        if not has_photo:
            missing_photo_ids.append(identity)

        links = item.get("also_on") or []
        if not isinstance(links, list):
            invalid_link_count += 1
            continue
        for link in links:
            if not isinstance(link, Mapping) or not str(link.get("id") or "").strip():
                invalid_link_count += 1
                continue
            actual_linked_ids.add(str(link["id"]).strip())
    actual_hidden_ids = actual_linked_ids - set(feed_ids)
    claimed_also_on_ids = {str(value) for value in (product.get("also_on_ids") or [])}
    if actual_hidden_ids != claimed_also_on_ids:
        errors.append("actual also_on identities differ from product.also_on_ids")
    unexplained_eligible_ids = sorted(
        set(str(value) for value in (product.get("eligible_ids") or []))
        - set(feed_ids)
        - actual_hidden_ids
    )
    if unexplained_eligible_ids:
        errors.append(
            "eligible identities missing without an actual also_on link: "
            + ", ".join(unexplained_eligible_ids[:5])
        )
    if invalid_link_count:
        errors.append(f"feed contains {invalid_link_count} invalid also_on links")

    if missing_description_ids:
        errors.append(
            f"feed visible listings missing description: {len(missing_description_ids)} "
            f"({', '.join(missing_description_ids[:5])})"
        )
    if missing_photo_ids:
        errors.append(
            f"feed visible listings missing photo: {len(missing_photo_ids)} "
            f"({', '.join(missing_photo_ids[:5])})"
        )

    eligible_ids = [str(value) for value in (product.get("eligible_ids") or [])]
    missing_active_ids = sorted(
        identity for identity in set(eligible_ids) if not current.get(identity, False)
    )
    if missing_active_ids:
        errors.append(
            "eligible identities are not active in database: "
            + ", ".join(missing_active_ids[:5])
        )

    raw_excluded_ids = product.get("excluded_ids")
    excluded_ids: dict[str, str] = {}
    if not isinstance(raw_excluded_ids, Mapping):
        errors.append("product.excluded_ids must be an object")
    else:
        for raw_identity, raw_reason in raw_excluded_ids.items():
            identity = str(raw_identity or "").strip()
            reason = str(raw_reason or "").strip()
            if not identity or not reason:
                errors.append("product.excluded_ids contains an empty identity or reason")
                continue
            if identity in excluded_ids:
                errors.append(
                    f"product.excluded_ids duplicate identity after normalization: {identity}"
                )
                continue
            excluded_ids[identity] = reason

    eligible_set = set(eligible_ids)
    excluded_set = set(excluded_ids)
    active_db_ids = {identity for identity, active in current.items() if active}
    overlap = sorted(eligible_set & excluded_set)
    if overlap:
        errors.append(
            "product eligible_ids and excluded_ids overlap: " + ", ".join(overlap[:5])
        )
    accounted_ids = eligible_set | excluded_set
    missing_accounting = sorted(active_db_ids - accounted_ids)
    if missing_accounting:
        errors.append(
            "active database identities missing from product accounting: "
            + ", ".join(missing_accounting[:5])
        )
    non_active_accounting = sorted(accounted_ids - active_db_ids)
    if non_active_accounting:
        errors.append(
            "product accounting contains non-active database identities: "
            + ", ".join(non_active_accounting[:5])
        )

    policy_exclusions = product.get("policy_exclusions")
    if isinstance(policy_exclusions, Mapping):
        claimed_reason_counts = {}
        for raw_reason, raw_count in policy_exclusions.items():
            try:
                claimed_reason_counts[str(raw_reason)] = int(raw_count)
            except (TypeError, ValueError):
                continue
        actual_reason_counts = dict(sorted(Counter(excluded_ids.values()).items()))
        if dict(sorted(claimed_reason_counts.items())) != actual_reason_counts:
            errors.append("product.policy_exclusions differs from excluded_ids reasons")

    policy_inputs = _db_policy_inputs(db_path)
    for identity, claimed_reason in sorted(excluded_ids.items()):
        candidate = policy_inputs.get(identity)
        if candidate is None:
            errors.append(f"excluded identity {identity} has no SQLite policy input")
            continue
        decision = evaluate_publication(candidate)
        if decision.eligible:
            errors.append(
                f"excluded identity {identity} is eligible under publication policy"
            )
        elif decision.reason != claimed_reason:
            errors.append(
                f"excluded identity {identity} reason mismatch: "
                f"claimed={claimed_reason} evaluated={decision.reason}"
            )

    if not isinstance(explanation_reasons, Mapping):
        errors.append("product.field_explanation_reasons must be an object")
        explanation_reasons = {}
    if isinstance(fields, Mapping):
        for name, raw_count in fields.items():
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if count and not str(explanation_reasons.get(name) or "").strip():
                errors.append(f"fields: missing explanation reason for {name}")

    # Preserve the first occurrence while avoiding noisy duplicate messages from
    # the strict core and the runtime evidence checks.
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    counts = dict(core.get("counts") or {})
    counts.update({f"database_{key}": value for key, value in database.items()})
    return {
        "ok": not errors,
        "run_id": run_id or None,
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
        "source_gate": source_gate,
        "report": report,
        "evidence": {
            "feed": str(feed_path),
            "source_manifests": str(manifests_path),
            "database": str(db_path),
            "before_db": str(before_db_path),
            "feed_visible_ids": len(feed_ids),
            "deleted_database_ids": len(deleted_ids),
        },
    }
