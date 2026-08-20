from __future__ import annotations

import json
from contextlib import closing
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from src.sqlite_atomic import atomic_sqlite_snapshot


ROOT = Path(__file__).resolve().parents[1]
PROMOTE = ROOT / "scripts" / "promote_db_candidate.py"
ROLLBACK = ROOT / "scripts" / "rollback_db_candidate.py"
DAILY = ROOT / "scripts" / "immo_daily_public_refresh.sh"


def _make_db(path: Path, rows: int, *, wal: bool = False) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    if wal:
        assert con.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute(
        "CREATE TABLE rental_listings "
        "(id TEXT PRIMARY KEY, source TEXT, is_active INTEGER)"
    )
    con.executemany(
        "INSERT INTO rental_listings VALUES (?, 'seloger', 1)",
        ((f"id-{i}",) for i in range(rows)),
    )
    con.commit()
    return con


def _count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM rental_listings").fetchone()[0])


def test_snapshot_reads_committed_rows_still_in_a_live_wal(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    snapshot = tmp_path / "snapshot.sqlite"
    live = _make_db(source, 12, wal=True)
    try:
        assert Path(f"{source}-wal").is_file()
        atomic_sqlite_snapshot(source, snapshot)
    finally:
        live.close()

    assert _count(snapshot) == 12
    with sqlite3.connect(snapshot) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_atomic_swap_uses_a_sibling_and_removes_stale_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "candidate.sqlite"
    target = tmp_path / "production.sqlite"
    _make_db(source, 11).close()
    _make_db(target, 10).close()
    Path(f"{target}-wal").write_bytes(b"stale-wal")
    Path(f"{target}-shm").write_bytes(b"stale-shm")

    real_replace = os.replace
    calls: list[tuple[Path, Path]] = []

    def observed_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        src_path, dst_path = Path(src), Path(dst)
        calls.append((src_path, dst_path))
        assert src_path.parent == target.parent
        assert dst_path == target
        assert _count(target) == 10
        real_replace(src_path, dst_path)

    monkeypatch.setattr("src.sqlite_atomic.os.replace", observed_replace)
    atomic_sqlite_snapshot(source, target)

    assert len(calls) == 1
    assert _count(target) == 11
    assert not Path(f"{target}-wal").exists()
    assert not Path(f"{target}-shm").exists()


def test_failed_atomic_swap_preserves_the_previous_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "candidate.sqlite"
    target = tmp_path / "production.sqlite"
    _make_db(source, 11).close()
    _make_db(target, 10).close()

    def fail_replace(_src: object, _dst: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr("src.sqlite_atomic.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        atomic_sqlite_snapshot(source, target)

    assert _count(target) == 10
    assert not list(tmp_path.glob(".production.sqlite.tmp-*"))


def test_real_failure_drill_restores_pre_promotion_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "production.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _make_db(target, 500).close()
    _make_db(candidate, 501).close()
    promote_json = tmp_path / "promote.json"

    promoted = subprocess.run(
        [
            sys.executable,
            str(PROMOTE),
            "--candidate",
            str(candidate),
            "--target",
            str(target),
            "--json-out",
            str(promote_json),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert promoted.returncode == 0, promoted.stdout + promoted.stderr
    promotion = json.loads(promote_json.read_text(encoding="utf-8"))
    assert _count(target) == 501

    # This is the same operation the EXIT trap must execute after any later gate fails.
    rollback_json = tmp_path / "rollback.json"
    rolled_back = subprocess.run(
        [
            sys.executable,
            str(ROLLBACK),
            "--apply",
            "--backup",
            promotion["backup"],
            "--target",
            str(target),
            "--json-out",
            str(rollback_json),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert rolled_back.returncode == 0, rolled_back.stdout + rolled_back.stderr
    rollback = json.loads(rollback_json.read_text(encoding="utf-8"))
    assert rollback["ok"] and rollback["mode"] == "apply"
    assert rollback["atomic_replace"] is True
    assert _count(target) == 500
    assert not Path(f"{target}-wal").exists()
    assert not Path(f"{target}-shm").exists()


def test_rollback_accepts_a_coherent_low_volume_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "production.sqlite"
    backup = tmp_path / "backup.sqlite"
    _make_db(target, 11).close()
    _make_db(backup, 10).close()
    report = tmp_path / "rollback-low-volume.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROLLBACK),
            "--apply",
            "--backup",
            str(backup),
            "--target",
            str(target),
            "--json-out",
            str(report),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _count(target) == 10
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["backup_check"]["active_rows"] == 10


def test_daily_failure_trap_never_copies_a_database_in_place() -> None:
    source = DAILY.read_text(encoding="utf-8")
    trap = source[
        source.index("restore_on_failure()") : source.index("trap restore_on_failure EXIT")
    ]
