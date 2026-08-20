"""Small durable JSON journals for filesystem publication transactions."""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
import tempfile


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        # Windows has no portable directory fsync; file fsync + replace is the
        # strongest portable contract available there.
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_journal(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_journal(path: Path) -> dict | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid promotion journal: {path}")
    return payload


def clear_journal(path: Path) -> None:
    path.unlink(missing_ok=True)
    fsync_directory(path.parent)


def require_schema(
    payload: dict, *, required: Iterable[str], optional: Iterable[str] = ()
) -> None:
    required_keys = set(required)
    allowed = required_keys | set(optional)
    missing = required_keys - set(payload)
    extra = set(payload) - allowed
    if missing or extra:
        raise RuntimeError(
            f"invalid promotion journal schema: missing={sorted(missing)} extra={sorted(extra)}"
        )


def confined_path(
    value: object,
    *,
    root: Path,
    field: str,
    direct_child: bool = False,
    name_prefix: str | None = None,
) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise RuntimeError(f"invalid promotion journal {field}: absolute path required")
    candidate = Path(value)
    root = root.resolve()
    if candidate.is_symlink():
        raise RuntimeError(f"invalid promotion journal {field}: symlink refused")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"invalid promotion journal {field}: path outside {root}"
        ) from exc
    if direct_child and resolved.parent != root:
        raise RuntimeError(f"invalid promotion journal {field}: direct sibling required")
    if name_prefix is not None and not resolved.name.startswith(name_prefix):
        raise RuntimeError(
            f"invalid promotion journal {field}: unexpected generated name"
        )
    cursor = candidate
    while True:
        if cursor.exists() and cursor.is_symlink():
            raise RuntimeError(f"invalid promotion journal {field}: symlink component refused")
        if cursor.resolve(strict=False) == root or cursor == cursor.parent:
            break
        cursor = cursor.parent
    return resolved


def child_names(value: object, *, field: str) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"invalid promotion journal {field}: list of names required")
    names = set(value)
    if len(names) != len(value):
        raise RuntimeError(f"invalid promotion journal {field}: duplicate name")
    for name in names:
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or Path(name).name != name
        ):
            raise RuntimeError(f"invalid promotion journal {field}: unsafe child name")
    return names
