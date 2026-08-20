#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from contextlib import closing
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.promotion_journal import (
    clear_journal, confined_path, read_journal, require_schema, write_journal,
)
from src.sqlite_atomic import (
    SIDECAR_SUFFIXES,
    atomic_sqlite_snapshot,
    sqlite_publication_lock,
)


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(path))


def integrity(path: Path) -> str:
    with closing(connect(path)) as con:
        row = con.execute("PRAGMA integrity_check").fetchone()
    return str(row[0] if row else "missing")


def source_column(con: sqlite3.Connection) -> str:
    cols = {row[1] for row in con.execute("PRAGMA table_info(rental_listings)")}
    if "source" in cols:
        return "source"
    if "source_site" in cols:
        return "source_site"
    raise SystemExit("candidate DB invalid: rental_listings lacks source/source_site column")


def active_counts(path: Path) -> dict[str, int]:
    with closing(connect(path)) as con:
        src_col = source_column(con)
        total = con.execute("SELECT COUNT(*) FROM rental_listings WHERE is_active=1").fetchone()[0]
        rows = con.execute(
            f"""
            SELECT COALESCE({src_col},'unknown') AS source_name, COUNT(*)
            FROM rental_listings
            WHERE is_active=1
            GROUP BY COALESCE({src_col},'unknown')
            ORDER BY COUNT(*) DESC, source_name
            """
        ).fetchall()
    return {"__total__": int(total), **{str(k): int(v) for k, v in rows}}


def validate_schema(path: Path) -> None:
    with closing(connect(path)) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"rental_listings"}
    missing = sorted(required - tables)
    if missing:
        raise SystemExit(f"candidate DB invalid: missing tables {missing}")


def pct_drop(before: int, after: int) -> float:
    if before <= 0:
        return 0.0 if after >= 0 else 100.0
    return max(0.0, (before - after) / before * 100.0)


def _prepare_report(json_out: Path, payload: dict) -> Path:
    json_out.parent.mkdir(parents=True, exist_ok=True)
    prepared = json_out.parent / f".{json_out.name}.prepared-{os.getpid()}"
    if prepared.exists():
        raise FileExistsError(f"prepared report already exists: {prepared}")
    try:
        prepared.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except BaseException:
        prepared.unlink(missing_ok=True)
        raise
    return prepared


def _install_prepared_report(prepared: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(prepared, destination)


def _write_report(json_out: Path, payload: dict) -> None:
    prepared = _prepare_report(json_out, payload)
    try:
        _install_prepared_report(prepared, json_out)
    finally:
        prepared.unlink(missing_ok=True)


def _capture_exact_target(target: Path) -> dict[Path, Path]:
    """Keep byte-exact rollback material in addition to the coherent backup."""
    copies: dict[Path, Path] = {}
    originals = [target, *(Path(f"{target}{suffix}") for suffix in SIDECAR_SUFFIXES)]
    try:
        for original in originals:
            if not original.exists():
                continue
            rollback = target.parent / f".{original.name}.exact-{os.getpid()}"
            if rollback.exists():
                raise FileExistsError(f"exact rollback file already exists: {rollback}")
            shutil.copy2(original, rollback)
            with rollback.open("r+b") as handle:
                os.fsync(handle.fileno())
            copies[original] = rollback
    except BaseException:
        for rollback in copies.values():
            rollback.unlink(missing_ok=True)
        raise
    return copies


def _restore_exact_target(target: Path, copies: dict[Path, Path]) -> None:
    for suffix in SIDECAR_SUFFIXES:
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    main_copy = copies.get(target)
    if main_copy is None:
        raise RuntimeError(f"exact target rollback missing: {target}")
    restored_main = target.parent / f".{target.name}.restoring-{os.getpid()}"
    shutil.copy2(main_copy, restored_main)
    with restored_main.open("r+b") as handle:
        os.fsync(handle.fileno())
    os.replace(restored_main, target)
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(f"{target}{suffix}")
        rollback = copies.get(sidecar)
        if rollback is not None:
            restored_sidecar = target.parent / f".{sidecar.name}.restoring-{os.getpid()}"
            shutil.copy2(rollback, restored_sidecar)
            with restored_sidecar.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(restored_sidecar, sidecar)


def _cleanup_exact_target(copies: dict[Path, Path]) -> None:
    for rollback in copies.values():
        rollback.unlink(missing_ok=True)


def _promotion_journal_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.promotion-journal.json")


def _write_pending_promotion_journal(
    *, target: Path, backup: Path, json_out: Path, exact_target: dict[Path, Path],
    prepared_report: Path | None = None,
) -> Path:
    journal = _promotion_journal_path(target)
    write_journal(
        journal,
        {
            "kind": "sqlite_promotion",
            "state": "prepared",
            "op": "promote_db",
            "target": str(target.resolve()),
            "backup": str(backup.resolve()),
            "json_out": str(json_out.resolve()),
            "prepared_report": str(prepared_report.resolve()) if prepared_report else None,
            "exact_target": {
                str(original.resolve()): str(copy.resolve())
                for original, copy in exact_target.items()
            },
        },
    )
    return journal


def _validate_db_journal(
    target: Path, payload: dict
) -> tuple[dict[Path, Path], Path, Path | None]:
    require_schema(
        payload,
        required={
            "kind", "op", "state", "target", "backup", "json_out",
            "prepared_report", "exact_target",
        },
    )
    if payload["kind"] != "sqlite_promotion" or payload["op"] != "promote_db":
        raise RuntimeError("invalid SQLite promotion journal kind/op")
    if payload["state"] not in {"prepared", "committed", "recovered"}:
        raise RuntimeError("invalid SQLite promotion journal state")
    if target.is_symlink():
        raise RuntimeError("invalid SQLite promotion journal target symlink")
    root = target.resolve().parent
    journal_target = confined_path(
        payload["target"], root=root, field="target", direct_child=True,
        name_prefix=target.name,
    )
    if journal_target != target.resolve():
        raise RuntimeError("invalid SQLite promotion journal target mismatch")
    confined_path(
        payload["backup"], root=root, field="backup", direct_child=True,
        name_prefix=f"{target.name}.pre-promote-",
    )
    report = confined_path(payload["json_out"], root=root, field="json_out")
    if report.suffix != ".json":
        raise RuntimeError("invalid SQLite promotion journal report name")
    prepared_report = None
    if payload["prepared_report"] is not None:
        prepared_report = confined_path(
            payload["prepared_report"], root=root, field="prepared_report",
            name_prefix=f".{report.name}.prepared-",
        )
    raw = payload["exact_target"]
    if not isinstance(raw, dict):
        raise RuntimeError("invalid SQLite promotion journal exact_target")
    allowed = {
        target.resolve(),
        *(Path(f"{target}{suffix}").resolve() for suffix in SIDECAR_SUFFIXES),
    }
    exact_target: dict[Path, Path] = {}
    for original_raw, copy_raw in raw.items():
        if not isinstance(original_raw, str) or Path(original_raw).resolve() not in allowed:
            raise RuntimeError("invalid SQLite promotion journal original path")
        original = Path(original_raw).resolve()
        copy = confined_path(
            copy_raw, root=root, field="exact_copy", direct_child=True,
            name_prefix=f".{original.name}.exact-",
        )
        exact_target[original] = copy
    if target.resolve() not in exact_target:
        raise RuntimeError("invalid SQLite promotion journal missing main copy")
    return exact_target, report, prepared_report


def _recover_pending_promotion_locked(target: Path) -> bool:
    journal = _promotion_journal_path(target)
    payload = read_journal(journal)
    if payload is None:
        return False
    exact_target, report, prepared_report = _validate_db_journal(target, payload)
    state = payload["state"]
    if state == "prepared":
        _restore_exact_target(target, exact_target)
        report.unlink(missing_ok=True)
        if prepared_report is not None:
            prepared_report.unlink(missing_ok=True)
        payload["state"] = "recovered"
        write_journal(journal, payload)
    _cleanup_exact_target(exact_target)
    clear_journal(journal)
    return True


def recover_pending_promotion(target: Path) -> bool:
    target = Path(target)
    with sqlite_publication_lock(target):
        return _recover_pending_promotion_locked(target)


def _promote_transaction_locked(
    *,
    candidate: Path,
    target: Path,
    backup: Path,
    json_out: Path,
    payload: dict,
) -> dict:
    _recover_pending_promotion_locked(target)
    if backup.exists():
        raise FileExistsError(f"promotion backup already exists: {backup}")
    committed_payload = {
        **payload,
        "promoted": True,
        "atomic_replace": True,
        "backup": str(backup),
    }
    prepared_report = _prepare_report(json_out, committed_payload)
    exact_target: dict[Path, Path] = {}
    journal_started = False
    try:
        exact_target = _capture_exact_target(target)
        _write_pending_promotion_journal(
            target=target,
            backup=backup,
            json_out=json_out,
            exact_target=exact_target,
            prepared_report=prepared_report,
        )
        journal_started = True
        atomic_sqlite_snapshot(target, backup)
        atomic_sqlite_snapshot(candidate, target)
        _install_prepared_report(prepared_report, json_out)
        prepared_report = None
        committed_journal = read_journal(_promotion_journal_path(target)) or {}
        committed_journal["state"] = "committed"
        write_journal(_promotion_journal_path(target), committed_journal)
        _cleanup_exact_target(exact_target)
        clear_journal(_promotion_journal_path(target))
        journal_started = False
        return committed_payload
    except BaseException:
        if journal_started:
            _recover_pending_promotion_locked(target)
            journal_started = False
        raise
    finally:
        if not journal_started:
            _cleanup_exact_target(exact_target)
        if prepared_report is not None:
            prepared_report.unlink(missing_ok=True)


def promote_transaction(
    *,
    candidate: Path,
    target: Path,
    backup: Path,
    json_out: Path,
    payload: dict,
) -> dict:
    """Own one lock across backup, target replacement, report and cleanup."""
    with sqlite_publication_lock(target):
        return _promote_transaction_locked(
            candidate=candidate,
            target=target,
            backup=backup,
            json_out=json_out,
            payload=payload,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote an immo stage SQLite DB after integrity and source-volume guards.")
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--max-drop-pct", type=float, default=15.0)
    ap.add_argument("--max-critical-drop-pct", type=float, default=35.0)
    ap.add_argument("--critical-source", action="append", default=["seloger"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    # Recover a SIGKILL-interrupted swap before reading either target or candidate.
    recover_pending_promotion(args.target)


    if not args.candidate.is_file():
        raise SystemExit(f"candidate DB missing: {args.candidate}")
    if not args.target.is_file():
        raise SystemExit(f"target DB missing: {args.target}")

    validate_schema(args.candidate)
    cand_integrity = integrity(args.candidate)
    target_integrity = integrity(args.target)
    errors: list[str] = []
    if cand_integrity.lower() != "ok":
        errors.append(f"candidate integrity_check={cand_integrity}")
    if target_integrity.lower() != "ok":
        errors.append(f"target integrity_check={target_integrity}")

    before = active_counts(args.target)
    after = active_counts(args.candidate)
    total_drop = pct_drop(before.get("__total__", 0), after.get("__total__", 0))
    if total_drop > args.max_drop_pct:
        errors.append(
            f"total active rows dropped {total_drop:.1f}% "
            f"({before.get('__total__', 0)} -> {after.get('__total__', 0)})"
        )
    for src in args.critical_source:
        b = before.get(src, 0)
        a = after.get(src, 0)
        drop = pct_drop(b, a)
        if b > 0 and a <= 0:
            errors.append(f"critical source {src} disappeared ({b} -> {a})")
        elif drop > args.max_critical_drop_pct:
            errors.append(f"critical source {src} dropped {drop:.1f}% ({b} -> {a})")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = args.target.with_name(args.target.name + f".pre-promote-{stamp}")

    payload = {
        "ok": not errors,
        "dry_run": args.dry_run,
        "promoted": False,
        "atomic_replace": False,
        "candidate": str(args.candidate),
        "target": str(args.target),
        "backup": None,
        "target_integrity": target_integrity,
        "candidate_integrity": cand_integrity,
        "before_counts": before,
        "after_counts": after,
        "total_drop_pct": round(total_drop, 3),
        "critical_sources": args.critical_source,
        "errors": errors,
    }
    if errors or args.dry_run:
        _write_report(args.json_out, payload)
    else:
        payload = promote_transaction(
            candidate=args.candidate,
            target=args.target,
            backup=backup,
            json_out=args.json_out,
            payload=payload,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if errors:
        print("PROMOTE_DB_CANDIDATE FAIL", file=sys.stderr)
        return 2
    print("PROMOTE_DB_CANDIDATE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
