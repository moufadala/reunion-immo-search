#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.promotion_journal import (
    child_names, clear_journal, confined_path, fsync_directory, read_journal,
    require_schema, write_journal,
)
from src.sqlite_atomic import sqlite_publication_lock
from media_link_copy import copytree_media_aware

REQUIRED = ["index.html", "listings.json"]


def validate_app(path: Path) -> dict:
    missing = [
        name
        for name in REQUIRED
        if (path / name).is_symlink()
        or not (path / name).is_file()
        or (path / name).stat().st_size == 0
    ]
    resolved_root = Path(os.path.abspath(path))
    for child in path.rglob("*"):
        if not child.is_symlink():
            continue
        raw_target = os.readlink(child)
        lexical_target = Path(os.path.abspath(child.parent / raw_target))
        try:
            common = Path(os.path.commonpath([resolved_root, lexical_target]))
        except ValueError:
            unsafe = True
        else:
            unsafe = common != resolved_root
        if not unsafe:
            try:
                resolved = child.resolve(strict=True)
            except (OSError, RuntimeError):
                if os.name != "nt":
                    unsafe = True
            else:
                unsafe = resolved != resolved_root and resolved_root not in resolved.parents
        if unsafe:
            missing.append(f"unsafe_symlink:{child.relative_to(path)}")
    count = None
    if not missing:
        data = json.loads((path / "listings.json").read_text(encoding="utf-8"))
        rows = data.get("listings", data if isinstance(data, list) else [])
        count = len(rows) if isinstance(rows, list) else None
        if not count:
            missing.append("listings.json:empty_or_invalid")
    return {"ok": not missing, "path": str(path), "missing": missing, "listing_count": count}


def make_public_readable(path: Path) -> None:
    """Set static-public modes without following links outside the app tree."""
    for root, dirs, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        os.chmod(root_path, 0o755)
        for name in dirs:
            child = root_path / name
            if child.is_symlink():
                continue
            os.chmod(child, 0o755)
        for name in files:
            child = root_path / name
            if child.is_symlink():
                continue
            os.chmod(child, 0o644)


def copy_tree(src: Path, dst: Path, *, media_mode: str) -> dict:
    if dst.exists():
        shutil.rmtree(dst)
    try:
        return copytree_media_aware(src, dst, media_mode=media_mode).to_dict()
    except BaseException:
        shutil.rmtree(dst, ignore_errors=True)
        raise


def _move_children(src: Path, dst: Path, moved: list[str]) -> None:
    for child in list(src.iterdir()):
        os.replace(child, dst / child.name)
        moved.append(child.name)
        fsync_directory(src)
        fsync_directory(dst)


def _restore_original(
    prepared: Path,
    target: Path,
    snapshot: Path,
    original: list[str],
    installed: list[str],
) -> None:
    for name in reversed(installed):
        os.replace(target / name, prepared / name)
    for name in reversed(original):
        os.replace(snapshot / name, target / name)
    snapshot.rmdir()


def transactional_child_swap(
    prepared: Path,
    target: Path,
    snapshot: Path,
    *,
    validator=validate_app,
) -> dict:
    """Swap validated children while preserving target inode and all old bytes."""
    if target.is_symlink() or prepared.is_symlink() or snapshot.is_symlink():
        raise ValueError("refuse symlink for rollback transaction paths")
    if snapshot.exists():
        raise FileExistsError(f"rollback snapshot already exists: {snapshot}")
    target.mkdir(parents=True, exist_ok=True)
    snapshot.mkdir(parents=False)
    original: list[str] = []
    installed: list[str] = []
    try:
        _move_children(target, snapshot, original)
    except BaseException:
        for name in reversed(original):
            os.replace(snapshot / name, target / name)
        snapshot.rmdir()
        raise
    try:
        _move_children(prepared, target, installed)
        installed_check = validator(target)
        if not installed_check.get("ok"):
            raise RuntimeError(f"post-swap validation failed: {installed_check.get('missing')}")
    except BaseException:
        _restore_original(prepared, target, snapshot, original, installed)
        raise
    prepared.rmdir()
    return installed_check


def _rollback_journal_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.rollback-journal.json")


def _write_pending_rollback_journal(
    *, target: Path, backup: Path, prepared: Path, snapshot: Path
) -> Path:
    journal = _rollback_journal_path(target)
    write_journal(
        journal,
        {
            "kind": "app_rollback",
            "op": "rollback_app",
            "state": "prepared",
            "target": str(target.resolve()),
            "backup": str(backup.resolve()),
            "prepared": str(prepared.resolve()),
            "snapshot": str(snapshot.resolve()),
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


def _validate_rollback_journal(
    target: Path, payload: dict
) -> tuple[Path, Path, Path, set[str], set[str]]:
    require_schema(
        payload,
        required={
            "kind", "op", "state", "target", "backup", "prepared",
            "snapshot", "original_names", "prepared_names",
        },
    )
    if payload["kind"] != "app_rollback" or payload["op"] != "rollback_app":
        raise RuntimeError("invalid app rollback journal kind/op")
    if payload["state"] not in {"prepared", "committed", "recovered"}:
        raise RuntimeError("invalid app rollback journal state")
    if target.is_symlink():
        raise RuntimeError("invalid app rollback journal target symlink")
    root = target.resolve().parent
    journal_target = confined_path(
        payload["target"], root=root, field="target", direct_child=True,
        name_prefix=target.name,
    )
    if journal_target != target.resolve():
        raise RuntimeError("invalid app rollback journal target mismatch")
    backup = confined_path(
        payload["backup"], root=root, field="backup", direct_child=True,
        name_prefix=f"{target.name}.pre-promote-",
    )
    prepared = confined_path(
        payload["prepared"], root=root, field="prepared", direct_child=True,
        name_prefix=f"{target.name}.rollback-prepared-",
    )
    snapshot = confined_path(
        payload["snapshot"], root=root, field="snapshot", direct_child=True,
        name_prefix=f"{target.name}.pre-rollback-",
    )
    original_names = child_names(payload["original_names"], field="original_names")
    prepared_names = child_names(payload["prepared_names"], field="prepared_names")
    return backup, prepared, snapshot, original_names, prepared_names


def _recover_pending_rollback_locked(target: Path) -> bool:
    journal = _rollback_journal_path(target)
    payload = read_journal(journal)
    if payload is None:
        return False
    _, prepared, snapshot, original_names, prepared_names = (
        _validate_rollback_journal(target, payload)
    )
    state = payload["state"]
    if state == "prepared":
        target.mkdir(parents=True, exist_ok=True)
        for name in original_names:
            saved = snapshot / name
            if saved.exists() or saved.is_symlink():
                _remove_entry(target / name)
                os.replace(saved, target / name)
        for name in prepared_names - original_names:
            _remove_entry(target / name)
        fsync_directory(target)
        if snapshot.exists():
            fsync_directory(snapshot)
        payload["state"] = "recovered"
        write_journal(journal, payload)
    if state in {"prepared", "recovered"}:
        shutil.rmtree(prepared, ignore_errors=True)
        shutil.rmtree(snapshot, ignore_errors=True)
    clear_journal(journal)
    return True


def recover_pending_rollback(target: Path) -> bool:
    target = Path(target)
    with sqlite_publication_lock(target):
        recovered = _recover_pending_rollback_locked(target)
        if recovered:
            restored = validate_app(target)
            if not restored.get("ok"):
                raise RuntimeError("recovered app rollback target is invalid")
        return recovered


def apply_rollback_transaction(
    *, target: Path, backup: Path, prepared: Path, snapshot: Path
) -> dict:
    """Journal and lock the destructive child-swap rollback window."""
    with sqlite_publication_lock(target):
        _recover_pending_rollback_locked(target)
        _write_pending_rollback_journal(
            target=target, backup=backup, prepared=prepared, snapshot=snapshot
        )
        journal = _rollback_journal_path(target)
        try:
            restored = transactional_child_swap(prepared, target, snapshot)
            committed = read_journal(journal) or {}
            committed["state"] = "committed"
            write_journal(journal, committed)
            clear_journal(journal)
            return restored
        except BaseException:
            _recover_pending_rollback_locked(target)
            raise
def main() -> int:
    ap = argparse.ArgumentParser(description="Validated rollback drill/apply for the public immo static app.")
    ap.add_argument("--backup", type=Path, required=True, help="Backup app directory to restore")
    ap.add_argument("--target", type=Path, default=Path("artifacts/app"), help="Public app directory")
    ap.add_argument("--apply", action="store_true", help="Actually replace target. Default is a non-destructive temp drill.")
    ap.add_argument("--qa-cmd", action="append", default=[], help="Extra command to run after restore/drill, relative to repo root")
    ap.add_argument("--json-out", type=Path)
    ap.add_argument(
        "--media-copy-mode",
        choices=["copy", "hardlink"],
        default=os.environ.get("IMMO_MEDIA_COPY_MODE", "hardlink"),
        help="copy mode for media files during rollback restore/snapshots",
    )
    args = ap.parse_args()
    # Recover any SIGKILL-interrupted rollback before reading backup or target.
    recover_pending_rollback(args.target)


    if args.backup.is_symlink() or not args.backup.exists() or not args.backup.is_dir():
        raise SystemExit(f"backup dir missing or unsafe: {args.backup}")
    if args.target.is_symlink():
        raise SystemExit(f"target symlink refused: {args.target}")
    backup_check = validate_app(args.backup)
    if not backup_check["ok"]:
        print(json.dumps(backup_check, ensure_ascii=False, indent=2))
        return 2

    root = Path(__file__).resolve().parents[1]
    restored_path: Path
    target_snapshot = None
    snapshot_stats = None
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target_snapshot = args.target.with_name(args.target.name + f".pre-rollback-{stamp}")
        restored_path = args.target.with_name(args.target.name + f".rollback-prepared-{stamp}")
    else:
        tmp_obj = tempfile.TemporaryDirectory(prefix="immo-rollback-drill-")
        restored_path = Path(tmp_obj.name) / "app"

    restore_stats = copy_tree(args.backup, restored_path, media_mode=args.media_copy_mode)
    mode = "apply" if args.apply else "drill"
    transaction = "drill_copy"
    try:
        if args.apply:
            make_public_readable(restored_path)
        restored_check = validate_app(restored_path)
        qa_results = []
        env = {**os.environ, "IMMO_APP_PATH": str(restored_path), "PYTHONPYCACHEPREFIX": "/tmp/pycache-hermes"}
        for cmd in args.qa_cmd:
            proc = subprocess.run(cmd, cwd=root, shell=True, text=True, capture_output=True, env=env, timeout=120)
            qa_results.append({"cmd": cmd, "returncode": proc.returncode, "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-2000:]})
            if proc.returncode != 0:
                restored_check["ok"] = False

        if args.apply and restored_check["ok"]:
            restored_check = apply_rollback_transaction(
                target=args.target,
                backup=args.backup,
                prepared=restored_path,
                snapshot=target_snapshot,
            )
            restored_path = args.target
            transaction = "prepared_child_swap"
        elif args.apply:
            shutil.rmtree(restored_path)
            transaction = "aborted_before_swap"
    except BaseException:
        if args.apply and restored_path != args.target:
            shutil.rmtree(restored_path, ignore_errors=True)
        raise

    payload = {
        "ok": bool(restored_check["ok"]),
        "mode": mode,
        "backup": str(args.backup),
        "target": str(args.target),
        "target_snapshot": str(target_snapshot) if target_snapshot else None,
        "media_copy_mode": args.media_copy_mode,
        "snapshot_copy_stats": snapshot_stats,
        "restore_copy_stats": restore_stats,
        "transaction": transaction,
        "restored_path": str(restored_path),
        "backup_check": backup_check,
        "restored_check": restored_check,
        "qa_results": qa_results,
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("ROLLBACK_DRILL PASS" if payload["ok"] else "ROLLBACK_DRILL FAIL", file=sys.stderr if not payload["ok"] else sys.stdout)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
