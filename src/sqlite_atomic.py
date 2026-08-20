"""Coherent SQLite snapshots with an atomic final pathname swap.

Every destination replacement owns a sibling inter-process lock. Callers that
compose multiple snapshots may hold ``sqlite_publication_lock`` around the full
transaction; the lock is re-entrant in the owning thread.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager
import threading
from typing import Iterator
from pathlib import Path


SIDECAR_SUFFIXES = ("-wal", "-shm")

class SQLitePublicationLocked(RuntimeError):
    """Another process or thread owns the destination publication window."""


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_LOCK_LOCAL = threading.local()


def _lock_file_for(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.publication.lock")


def _try_os_lock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_os(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def sqlite_publication_lock(destination: Path) -> Iterator[None]:
    """Own a fail-closed, re-entrant publication lock for ``destination``."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_file_for(destination)
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.RLock())
    if not thread_lock.acquire(blocking=False):
        raise SQLitePublicationLocked(f"SQLite publication is busy: {destination}")
    held = getattr(_LOCK_LOCAL, "held", {})
    owner_pid = os.getpid()
    if held.get(key) == owner_pid:
        try:
            yield
        finally:
            thread_lock.release()
        return
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            _try_os_lock(descriptor)
        except OSError as exc:
            raise SQLitePublicationLocked(
                f"SQLite publication is busy: {destination}"
            ) from exc
        held[key] = owner_pid
        _LOCK_LOCAL.held = held
        try:
            yield
        finally:
            del held[key]
            _unlock_os(descriptor)
    finally:
        os.close(descriptor)
        thread_lock.release()


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        # Windows does not expose a portable directory fsync operation.
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_sidecars(path: Path) -> None:
    for suffix in SIDECAR_SUFFIXES:
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _quiesce_existing_target(path: Path) -> None:
    if not path.exists():
        _remove_sidecars(path)
        return

    # Fold committed WAL pages into the old main file before removing its
    # sidecars. A busy result fails closed instead of racing another writer.
    with closing(sqlite3.connect(str(path), timeout=0.0)) as connection:
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result and int(result[0]) != 0:
            raise RuntimeError(f"destination SQLite WAL is busy: {path}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise RuntimeError(f"destination SQLite is not coherent before swap: {path}")

    _remove_sidecars(path)
    _fsync_directory(path.parent)


def _atomic_sqlite_snapshot_unlocked(source: Path, destination: Path) -> None:
    """Publish a coherent snapshot of ``source`` at ``destination``.

    The snapshot is built and fsynced beside the destination, then installed by
    one ``os.replace``. If the replace fails, the previous destination remains
    intact and the temporary file is removed.
    """
    source = Path(source)
    destination = Path(destination)
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source missing: {source}")
    if source.resolve() == destination.resolve():
        raise ValueError("SQLite source and destination must be different paths")

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.tmp-", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(str(source))) as source_connection:
            with closing(sqlite3.connect(str(temporary))) as destination_connection:
                source_connection.backup(destination_connection)
                check = destination_connection.execute("PRAGMA integrity_check").fetchone()
                if not check or str(check[0]).lower() != "ok":
                    raise RuntimeError(
                        f"temporary SQLite snapshot failed integrity_check: {source}"
                    )

        reference = destination if destination.exists() else source
        os.chmod(temporary, stat.S_IMODE(reference.stat().st_mode))
        _fsync_file(temporary)
        _quiesce_existing_target(destination)
        os.replace(temporary, destination)
        _remove_sidecars(destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_sqlite_snapshot(source: Path, destination: Path) -> None:
    """Publish a coherent snapshot while owning the destination lock."""
    with sqlite_publication_lock(destination):
        _atomic_sqlite_snapshot_unlocked(source, destination)
