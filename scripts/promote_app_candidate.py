#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.promotion_journal import (
    child_names, clear_journal, confined_path, fsync_directory, read_journal,
    require_schema, write_journal,
)


from media_link_copy import copytree_media_aware
from rollback_public_app import make_public_readable, transactional_child_swap


def validate_app(path: Path) -> dict:
    # C.2: the clean stage is headless. `listings.json` is the only public bus
    # promoted here; index.html/feed.json/v2 are rebuilt immediately after
    # promotion by build_product_v2.sh (IMMO_V2_AS_ROOT=1).
    for name in ("listings.json",):
        p = path / name
        if not p.is_file() or p.stat().st_size == 0:
            raise SystemExit(f"candidate invalid: missing/non-empty {p}")
    return {"ok": True, "missing": []}


def _report_staging_parent(json_out: Path, target: Path) -> Path:
    """Keep a prepared report outside the child-swap tree."""
    resolved_target = target.resolve()
    resolved_parent = json_out.parent.resolve()
    inside_target = (
        resolved_parent == resolved_target or resolved_target in resolved_parent.parents
    )
    return target.parent if inside_target else json_out.parent


def _prepare_report(json_out: Path, target: Path, payload: dict) -> Path:
    staging_parent = _report_staging_parent(json_out, target)
    staging_parent.mkdir(parents=True, exist_ok=True)
    prepared = staging_parent / f".{json_out.name}.prepared-{os.getpid()}"
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


def _write_report(json_out: Path, target: Path, payload: dict) -> None:
    prepared = _prepare_report(json_out, target, payload)
    try:
        _install_prepared_report(prepared, json_out)
    finally:
        prepared.unlink(missing_ok=True)

def _promotion_journal_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.promotion-journal.json")


def _write_pending_promotion_journal(
    *, target: Path, prepared: Path, backup: Path, json_out: Path,
    prepared_report: Path | None = None,
) -> Path:
    journal = _promotion_journal_path(target)
    write_journal(
        journal,
        {
            "kind": "app_promotion",
            "state": "prepared",
            "op": "promote_app",
            "target": str(target.resolve()),
            "prepared": str(prepared.resolve()),
            "backup": str(backup.resolve()),
            "json_out": str(json_out.resolve()),
            "prepared_report": str(prepared_report.resolve()) if prepared_report else None,
            "original_names": sorted(child.name for child in target.iterdir()),
            "prepared_names": sorted(child.name for child in prepared.iterdir()),
        },
    )
    return journal


def _remove_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _validate_app_journal(
    target: Path, payload: dict
) -> tuple[Path, Path, Path, Path | None, set[str], set[str]]:
    require_schema(
        payload,
        required={
            "kind", "op", "state", "target", "prepared", "backup",
            "json_out", "prepared_report", "original_names", "prepared_names",
        },
    )
    if payload["kind"] != "app_promotion" or payload["op"] != "promote_app":
        raise RuntimeError("invalid app promotion journal kind/op")
    if payload["state"] not in {"prepared", "committed", "recovered"}:
        raise RuntimeError("invalid app promotion journal state")
    if target.is_symlink():
        raise RuntimeError("invalid app promotion journal target symlink")
    root = target.resolve().parent
    journal_target = confined_path(
        payload["target"], root=root, field="target", direct_child=True,
        name_prefix=target.name,
    )
    if journal_target != target.resolve():
        raise RuntimeError("invalid app promotion journal target mismatch")
    prepared = confined_path(
        payload["prepared"], root=root, field="prepared", direct_child=True,
        name_prefix=f".{target.name}.promote-prepared-",
    )
    backup = confined_path(
        payload["backup"], root=root, field="backup", direct_child=True,
        name_prefix=f"{target.name}.pre-promote-",
    )
    report = confined_path(payload["json_out"], root=root, field="json_out")
    if report.suffix != ".json":
        raise RuntimeError("invalid app promotion journal report name")
    prepared_report = None
    if payload["prepared_report"] is not None:
        prepared_report = confined_path(
            payload["prepared_report"], root=root, field="prepared_report",
            name_prefix=f".{report.name}.prepared-",
        )
    original_names = child_names(payload["original_names"], field="original_names")
    prepared_names = child_names(payload["prepared_names"], field="prepared_names")
    return backup, prepared, report, prepared_report, original_names, prepared_names


def _recover_pending_promotion(target: Path) -> bool:
    journal = _promotion_journal_path(target)
    payload = read_journal(journal)
    if payload is None:
        return False
    (
        backup, prepared, report, prepared_report, original_names, prepared_names,
    ) = _validate_app_journal(target, payload)
    state = payload["state"]
    if state == "prepared":
        target.mkdir(parents=True, exist_ok=True)
        for name in original_names:
            saved = backup / name
            if saved.exists() or saved.is_symlink():
                _remove_entry(target / name)
                os.replace(saved, target / name)
        for name in prepared_names - original_names:
            _remove_entry(target / name)
        fsync_directory(target)
        if backup.exists():
            fsync_directory(backup)
        report.unlink(missing_ok=True)
        if prepared_report is not None:
            prepared_report.unlink(missing_ok=True)
        payload["state"] = "recovered"
        write_journal(journal, payload)
    if prepared.exists():
        shutil.rmtree(prepared)
    if state in {"prepared", "recovered"} and backup.exists():
        shutil.rmtree(backup)
    clear_journal(journal)
    return True


def recover_pending_promotion(target: Path) -> bool:
    recovered = _recover_pending_promotion(Path(target))
    if recovered:
        validate_app(Path(target))
    return recovered



def promote_transaction(
    *,
    candidate: Path,
    target: Path,
    backup: Path,
    json_out: Path,
    payload: dict,
    media_copy_mode: str,
) -> dict:
    """Prepare off-tree and journal the child swap before publishing it."""
    recover_pending_promotion(target)
    if backup.exists():
        raise FileExistsError(f"promotion backup already exists: {backup}")
    prepared_app = target.with_name(f".{target.name}.promote-prepared-{os.getpid()}")
    failed_snapshot = target.with_name(f".{target.name}.failed-promote-{os.getpid()}")
    if prepared_app.exists() or failed_snapshot.exists():
        raise FileExistsError("stale app promotion transaction path exists")

    prepared_report: Path | None = None
    swapped = False
    journal_started = False
    try:
        promote_stats = copytree_media_aware(
            candidate, prepared_app, media_mode=media_copy_mode
        )
        make_public_readable(prepared_app)
        validate_app(prepared_app)
        committed_payload = {
            **payload,
            "promoted": True,
            "backup": str(backup),
            "backup_copy_stats": {"transaction": "prepared_child_swap"},
            "promote_copy_stats": promote_stats.to_dict(),
        }
        prepared_report = _prepare_report(json_out, target, committed_payload)
        _write_pending_promotion_journal(
            target=target,
            prepared=prepared_app,
            backup=backup,
            json_out=json_out,
            prepared_report=prepared_report,
        )
        journal_started = True
        transactional_child_swap(
            prepared_app, target, backup, validator=validate_app
        )
        swapped = True
        _install_prepared_report(prepared_report, json_out)
        prepared_report = None
        committed_journal = read_journal(_promotion_journal_path(target)) or {}
        committed_journal["state"] = "committed"
        write_journal(_promotion_journal_path(target), committed_journal)
        clear_journal(_promotion_journal_path(target))
        journal_started = False
        return committed_payload
    except BaseException:
        if journal_started:
            recover_pending_promotion(target)
            swapped = False
            journal_started = False
        if not swapped:
            shutil.rmtree(prepared_app, ignore_errors=True)
        raise
    finally:
        if prepared_report is not None:
            prepared_report.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote a generated immo static app only after volume/source guard passes.")
    ap.add_argument("--candidate", type=Path, required=True, help="Generated app directory to promote")
    ap.add_argument("--target", type=Path, default=Path("artifacts/app"), help="Published app directory")
    ap.add_argument("--max-drop-pct", type=float, default=15.0)
    ap.add_argument("--max-critical-drop-pct", type=float, default=35.0)
    ap.add_argument("--critical-source", action="append", default=["seloger"])
    ap.add_argument("--json-out", type=Path, default=Path("artifacts/app/promote_guard.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--media-copy-mode",
        choices=["copy", "hardlink"],
        default=os.environ.get("IMMO_MEDIA_COPY_MODE", "copy"),
        help="copy mode for media files during backup/promotion; default keeps legacy copy behavior",
    )
    args = ap.parse_args()

    # A prior SIGKILL may have left a partial child swap; recover before the guard reads it.
    recover_pending_promotion(args.target)

    root = Path(__file__).resolve().parents[1]
    validate_app(args.candidate)
    if not args.target.exists():
        raise SystemExit(f"target missing for baseline guard: {args.target}")

    guard_out = args.json_out.with_name(args.json_out.stem + ".delta.json")
    cmd = [
        sys.executable, str(root / "scripts" / "audit_public_delta_guard.py"),
        "--baseline", str(args.target),
        "--candidate", str(args.candidate),
        "--max-drop-pct", str(args.max_drop_pct),
        "--max-critical-drop-pct", str(args.max_critical_drop_pct),
        "--json-out", str(guard_out),
    ]
    for src in args.critical_source:
        cmd += ["--critical-source", src]
    proc = subprocess.run(cmd, cwd=root, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = args.target.with_name(args.target.name + f".pre-promote-{stamp}")

    payload = {
        "ok": True,
        "dry_run": args.dry_run,
        "promoted": False,
        "candidate": str(args.candidate),
        "target": str(args.target),
        "backup": None,
        "media_copy_mode": args.media_copy_mode,
        "backup_copy_stats": None,
        "promote_copy_stats": None,
        "guard_json": str(guard_out),
        "guard_stdout_tail": proc.stdout[-2000:],
    }
    if args.dry_run:
        _write_report(args.json_out, args.target, payload)
    else:
        payload = promote_transaction(
            candidate=args.candidate,
            target=args.target,
            backup=backup,
            json_out=args.json_out,
            payload=payload,
            media_copy_mode=args.media_copy_mode,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("PROMOTE_APP_CANDIDATE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
