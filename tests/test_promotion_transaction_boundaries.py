from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_app_candidate
import promote_db_candidate


def _write_app(path: Path, marker: str) -> None:
    (path / "thumbs").mkdir(parents=True)
    (path / "index.html").write_text(f"<html>{marker}</html>", encoding="utf-8")
    (path / "listings.json").write_text(
        json.dumps({"listings": [{"id": marker, "source": "seloger"}]}),
        encoding="utf-8",
    )
    (path / "thumbs" / "one.jpg").write_bytes(f"jpeg-{marker}".encode())


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _app_transaction(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[str, bytes], int]:
    target = tmp_path / "app"
    candidate = tmp_path / "candidate"
    backup = tmp_path / "app.pre-promote-test"
    report = tmp_path / "promote-app.json"
    _write_app(target, "old")
    _write_app(candidate, "new")
    return target, candidate, backup, report, _tree_bytes(target), target.stat().st_ino


def _promote_app(target: Path, candidate: Path, backup: Path, report: Path) -> dict:
    return promote_app_candidate.promote_transaction(
        candidate=candidate,
        target=target,
        backup=backup,
        json_out=report,
        payload={"ok": True, "dry_run": False, "promoted": True},
        media_copy_mode="copy",
    )


@pytest.mark.parametrize("failure_point", ["copy", "chmod", "installed_validation", "report"])
def test_app_failure_at_every_transaction_boundary_restores_exact_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    target, candidate, backup, report, before, inode = _app_transaction(tmp_path)

    if failure_point == "copy":
        def partial_copy(_src, dst, **_kwargs):
            dst.mkdir(parents=True)
            (dst / "partial").write_bytes(b"partial")
            raise OSError("injected preparation copy failure")

        monkeypatch.setattr(promote_app_candidate, "copytree_media_aware", partial_copy)
    elif failure_point == "chmod":
        monkeypatch.setattr(
            promote_app_candidate,
            "make_public_readable",
            lambda _path: (_ for _ in ()).throw(OSError("injected chmod failure")),
        )
    elif failure_point == "installed_validation":
        real_validate = promote_app_candidate.validate_app
        calls = 0

        def fail_after_install(path: Path):
            nonlocal calls
            calls += 1
            result = real_validate(path)
            if calls == 2:
                raise OSError("injected post-swap validation failure")
            return result

        monkeypatch.setattr(promote_app_candidate, "validate_app", fail_after_install)
    else:
        monkeypatch.setattr(
            promote_app_candidate,
            "_install_prepared_report",
            lambda _prepared, _destination: (_ for _ in ()).throw(OSError("injected report failure")),
        )

    with pytest.raises(OSError, match="injected"):
        _promote_app(target, candidate, backup, report)

    assert _tree_bytes(target) == before
    assert target.stat().st_ino == inode
    assert not report.exists()


def test_app_success_preserves_root_inode_and_keeps_usable_backup(tmp_path: Path) -> None:
    target, candidate, backup, report, before, inode = _app_transaction(tmp_path)
    expected = _tree_bytes(candidate)

    payload = _promote_app(target, candidate, backup, report)

    assert target.stat().st_ino == inode
    assert _tree_bytes(target) == expected
    assert _tree_bytes(backup) == before
    assert promote_app_candidate.validate_app(backup)["ok"] is True
    assert payload["promoted"] is True
    assert json.loads(report.read_text(encoding="utf-8"))["promoted"] is True


def _make_db(path: Path, rows: int) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.execute(
            "CREATE TABLE rental_listings "
            "(id TEXT PRIMARY KEY, source TEXT, is_active INTEGER)"
        )
        con.executemany(
            "INSERT INTO rental_listings VALUES (?, 'seloger', 1)",
            ((f"id-{i}",) for i in range(rows)),
        )
        con.commit()


def _db_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as con:
        return int(con.execute("SELECT COUNT(*) FROM rental_listings").fetchone()[0])


def _db_transaction(tmp_path: Path) -> tuple[Path, Path, Path, Path, bytes]:
    target = tmp_path / "production.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    backup = tmp_path / "production.sqlite.pre-promote-test"
    report = tmp_path / "promote-db.json"
    _make_db(target, 10)
    _make_db(candidate, 11)
    return target, candidate, backup, report, target.read_bytes()


def _promote_db(target: Path, candidate: Path, backup: Path, report: Path) -> dict:
    return promote_db_candidate.promote_transaction(
        candidate=candidate,
        target=target,
        backup=backup,
        json_out=report,
        payload={"ok": True, "dry_run": False, "promoted": True},
    )


@pytest.mark.parametrize("failure_point", ["backup", "swap", "report"])
def test_db_failure_at_every_transaction_boundary_restores_exact_target_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    target, candidate, backup, report, before = _db_transaction(tmp_path)

    if failure_point in {"backup", "swap"}:
        real_snapshot = promote_db_candidate.atomic_sqlite_snapshot
        calls = 0

        def injected_snapshot(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == (1 if failure_point == "backup" else 2):
                raise OSError(f"injected {failure_point} failure")
            real_snapshot(source, destination)

        monkeypatch.setattr(promote_db_candidate, "atomic_sqlite_snapshot", injected_snapshot)
    else:
        monkeypatch.setattr(
            promote_db_candidate,
            "_install_prepared_report",
            lambda _prepared, _destination: (_ for _ in ()).throw(OSError("injected report failure")),
        )

    with pytest.raises(OSError, match="injected"):
        _promote_db(target, candidate, backup, report)

    assert target.read_bytes() == before
    assert _db_count(target) == 10
    assert not report.exists()


def test_db_success_keeps_coherent_backup_and_installs_report(tmp_path: Path) -> None:
    target, candidate, backup, report, _before = _db_transaction(tmp_path)

    payload = _promote_db(target, candidate, backup, report)

    assert _db_count(target) == 11
    assert _db_count(backup) == 10
    assert payload["promoted"] is True
    assert payload["backup"] == str(backup)
    assert json.loads(report.read_text(encoding="utf-8"))["promoted"] is True
