#!/usr/bin/env python3
"""Durable transaction owner for the public DB/app/history publication set.

Local promotion journals protect each individual rename.  This journal owns the
larger business transaction: until ``commit`` is durable, a later official run
restores all three public artifacts from the same pre-run snapshot.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from media_link_copy import copytree_media_aware
from rollback_public_app import (
    apply_rollback_transaction,
    recover_pending_rollback,
    validate_app,
)
from src.promotion_journal import (
    clear_journal,
    confined_path,
    read_journal,
    require_schema,
    write_journal,
)
from src.sqlite_atomic import atomic_sqlite_snapshot, sqlite_publication_lock


KIND = "pipeline_publication"
OP = "publish_db_app_history"
STATES = {"prepared", "committed", "recovered"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _safe_run_id(value: str) -> str:
    if not value or not RUN_ID_RE.fullmatch(value):
        raise RuntimeError("run-id must contain only letters, digits, dot, dash, underscore")
    return value


def _assert_sqlite(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"SQLite artifact missing or unsafe: {path}")
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    if not row or str(row[0]).lower() != "ok":
        raise RuntimeError(f"SQLite integrity_check failed: {path}")


def _paths(args: argparse.Namespace) -> dict[str, Path]:
    run_id = _safe_run_id(args.run_id)
    db = args.db_target.resolve()
    app = args.app_target.resolve()
    history = args.history_target.resolve()
    return {
        "db_target": db,
        "app_target": app,
        "history_target": history,
        "db_backup": db.with_name(f"{db.name}.global-pre-run-{run_id}"),
        # The prefix is deliberately compatible with rollback_public_app's
        # independently validated backup contract.
        "app_backup": app.with_name(f"{app.name}.pre-promote-global-{run_id}"),
        "history_backup": history.with_name(
            f"{history.name}.global-pre-run-{run_id}"
        ),
    }


def _locks(paths: dict[str, Path]) -> ExitStack:
    stack = ExitStack()
    try:
        for target in sorted(
            (paths["db_target"], paths["app_target"], paths["history_target"]),
            key=lambda item: str(item),
        ):
            stack.enter_context(sqlite_publication_lock(target))
    except BaseException:
        stack.close()
        raise
    return stack


def _validate_payload(
    args: argparse.Namespace, payload: dict, expected: dict[str, Path]
) -> dict[str, Path]:
    require_schema(
        payload,
        required={
            "kind", "op", "state", "run_id", "db_target", "app_target",
            "history_target", "db_backup", "app_backup", "history_backup",
        },
    )
    if payload["kind"] != KIND or payload["op"] != OP:
        raise RuntimeError("invalid global publication journal kind/op")
    if payload["state"] not in STATES:
        raise RuntimeError("invalid global publication journal state")
    if payload["run_id"] != _safe_run_id(args.run_id):
        raise RuntimeError("global publication journal run-id mismatch")

    validated: dict[str, Path] = {}
    target_fields = ("db_target", "app_target", "history_target")
    for field in target_fields:
        target = expected[field]
        if target.is_symlink():
            raise RuntimeError(f"invalid global publication journal {field} symlink")
        value = confined_path(
            payload[field], root=target.parent, field=field, direct_child=True
        )
        if value != target:
            raise RuntimeError(f"global publication journal {field} mismatch")
        validated[field] = value

    prefixes = {
        "db_backup": f"{expected['db_target'].name}.global-pre-run-",
        "app_backup": f"{expected['app_target'].name}.pre-promote-global-",
        "history_backup": f"{expected['history_target'].name}.global-pre-run-",
    }
    target_for_backup = {
        "db_backup": "db_target",
        "app_backup": "app_target",
        "history_backup": "history_target",
    }
    for field, prefix in prefixes.items():
        target = expected[target_for_backup[field]]
        value = confined_path(
            payload[field], root=target.parent, field=field, direct_child=True,
            name_prefix=prefix,
        )
        if value != expected[field]:
            raise RuntimeError(f"global publication journal {field} mismatch")
        validated[field] = value
    return validated


def _remove_generated(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"refuse generated symlink cleanup: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _cleanup_backups(paths: dict[str, Path]) -> None:
    for field in ("db_backup", "app_backup", "history_backup"):
        _remove_generated(paths[field])


def begin(args: argparse.Namespace) -> dict:
    expected = _paths(args)
    journal = args.journal.resolve()
    with _locks(expected):
        if read_journal(journal) is not None:
            raise RuntimeError(
                "unfinished global publication transaction; recover it before begin"
            )
        for field in ("db_backup", "app_backup", "history_backup"):
            if expected[field].exists() or expected[field].is_symlink():
                raise RuntimeError(f"global transaction backup already exists: {expected[field]}")
        _assert_sqlite(expected["db_target"])
        _assert_sqlite(expected["history_target"])
        app_check = validate_app(expected["app_target"])
        if not app_check.get("ok"):
            raise RuntimeError(f"public app invalid before transaction: {app_check}")

        try:
            atomic_sqlite_snapshot(expected["db_target"], expected["db_backup"])
            atomic_sqlite_snapshot(
                expected["history_target"], expected["history_backup"]
            )
            copytree_media_aware(
                expected["app_target"], expected["app_backup"], media_mode="hardlink"
            )
            _assert_sqlite(expected["db_backup"])
            _assert_sqlite(expected["history_backup"])
            backup_check = validate_app(expected["app_backup"])
            if not backup_check.get("ok"):
                raise RuntimeError(f"global app backup invalid: {backup_check}")
            payload = {
                "kind": KIND,
                "op": OP,
                "state": "prepared",
                "run_id": args.run_id,
                **{name: str(path) for name, path in expected.items()},
            }
            write_journal(journal, payload)
        except BaseException:
            _cleanup_backups(expected)
            raise
    return {"ok": True, "operation": "begin", "state": "prepared"}


def _restore_app(paths: dict[str, Path], run_id: str) -> None:
    target = paths["app_target"]
    backup = paths["app_backup"]
    recover_pending_rollback(target)
    prepared = target.with_name(f"{target.name}.rollback-prepared-global-{run_id}")
    snapshot = target.with_name(f"{target.name}.pre-rollback-global-{run_id}")
    for generated in (prepared, snapshot):
        _remove_generated(generated)
    copytree_media_aware(backup, prepared, media_mode="hardlink")
    apply_rollback_transaction(
        target=target, backup=backup, prepared=prepared, snapshot=snapshot
    )


def recover(args: argparse.Namespace) -> dict:
    journal = args.journal.resolve()
    # Startup recovery necessarily runs under a *new* STAMP.  Derive backup
    # names from the durable owner recorded by the prior run, never from the
    # new caller and never from unvalidated journal paths.
    initial = read_journal(journal)
    if initial is None:
        return {"ok": True, "operation": "recover", "state": "none"}
    recorded_run_id = _safe_run_id(initial.get("run_id", ""))
    recovery_args = argparse.Namespace(**vars(args))
    recovery_args.run_id = recorded_run_id
    expected = _paths(recovery_args)
    with _locks(expected):
        payload = read_journal(journal)
        if payload is None:
            return {"ok": True, "operation": "recover", "state": "none"}
        paths = _validate_payload(recovery_args, payload, expected)
        state = payload["state"]
        if state == "prepared":
            _assert_sqlite(paths["db_backup"])
            _assert_sqlite(paths["history_backup"])
            backup_check = validate_app(paths["app_backup"])
            if not backup_check.get("ok"):
                raise RuntimeError(f"global app backup invalid during recovery: {backup_check}")
            atomic_sqlite_snapshot(paths["db_backup"], paths["db_target"])
            atomic_sqlite_snapshot(
                paths["history_backup"], paths["history_target"]
            )
            _restore_app(paths, recorded_run_id)
            _assert_sqlite(paths["db_target"])
            _assert_sqlite(paths["history_target"])
            restored = validate_app(paths["app_target"])
            if not restored.get("ok"):
                raise RuntimeError(f"global app recovery invalid: {restored}")
            payload["state"] = "recovered"
            write_journal(journal, payload)
        _cleanup_backups(paths)
        clear_journal(journal)
    return {"ok": True, "operation": "recover", "state": "recovered"}


def commit(args: argparse.Namespace) -> dict:
    expected = _paths(args)
    journal = args.journal.resolve()
    with _locks(expected):
        payload = read_journal(journal)
        if payload is None:
            raise RuntimeError("global publication transaction is not prepared")
        paths = _validate_payload(args, payload, expected)
        if payload["state"] != "prepared":
            raise RuntimeError(f"cannot commit global transaction in {payload['state']} state")
        payload["state"] = "committed"
        write_journal(journal, payload)
        _cleanup_backups(paths)
        clear_journal(journal)
    return {"ok": True, "operation": "commit", "state": "committed"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("begin", "recover", "commit"))
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--db-target", required=True, type=Path)
    parser.add_argument("--app-target", required=True, type=Path)
    parser.add_argument("--history-target", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = globals()[args.operation](args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
