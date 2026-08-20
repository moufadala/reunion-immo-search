from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_app_candidate
import promote_db_candidate
import rollback_db_candidate
import rollback_public_app
from rollback_public_app import transactional_child_swap
from src.promotion_journal import write_journal
from src.sqlite_atomic import SQLitePublicationLocked, atomic_sqlite_snapshot


def _make_db(path: Path, rows: int) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE rental_listings "
            "(id TEXT PRIMARY KEY, source TEXT, is_active INTEGER)"
        )
        connection.executemany(
            "INSERT INTO rental_listings VALUES (?, 'seloger', 1)",
            ((f"id-{index}",) for index in range(rows)),
        )
        connection.commit()


def _db_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM rental_listings").fetchone()[0])


def _app(path: Path, marker: str) -> None:
    path.mkdir()
    (path / "listings.json").write_text(
        json.dumps({"listings": [{"id": marker}]}), encoding="utf-8"
    )
    (path / "index.html").write_text(marker, encoding="utf-8")
    (path / f"only-{marker}").write_text(marker, encoding="utf-8")


def test_next_db_promotion_recovers_a_prepared_journal_left_after_swap(tmp_path: Path) -> None:
    target = tmp_path / "production.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    backup = tmp_path / "production.sqlite.pre-promote-test"
    report = tmp_path / "promote.json"
    _make_db(target, 10)
    _make_db(candidate, 11)

    exact = promote_db_candidate._capture_exact_target(target)
    promote_db_candidate._write_pending_promotion_journal(
        target=target, backup=backup, json_out=report, exact_target=exact
    )
    atomic_sqlite_snapshot(target, backup)
    atomic_sqlite_snapshot(candidate, target)
    report.write_text('{"promoted": true}', encoding="utf-8")

    assert _db_count(target) == 11
    restarted = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "promote_db_candidate.py"),
            "--candidate",
            str(tmp_path / "missing.sqlite"),
            "--target",
            str(target),
            "--json-out",
            str(tmp_path / "restart.json"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert restarted.returncode != 0
    assert _db_count(target) == 10
    assert not report.exists()
    assert not promote_db_candidate._promotion_journal_path(target).exists()
    assert all(not rollback.exists() for rollback in exact.values())


def test_next_app_promotion_recovers_partial_or_complete_uncommitted_swap(tmp_path: Path) -> None:
    target = tmp_path / "app"
    prepared = tmp_path / ".app.promote-prepared-test"
    backup = tmp_path / "app.pre-promote-test"
    report = tmp_path / "promote-app.json"
    _app(target, "old")
    _app(prepared, "new")
    promote_app_candidate._write_pending_promotion_journal(
        target=target, prepared=prepared, backup=backup, json_out=report
    )
    transactional_child_swap(prepared, target, backup, validator=lambda _: {"ok": True})
    report.write_text('{"promoted": true}', encoding="utf-8")

    restarted = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "promote_app_candidate.py"),
            "--candidate",
            str(tmp_path / "missing-app"),
            "--target",
            str(target),
            "--json-out",
            str(tmp_path / "restart-app.json"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert restarted.returncode != 0
    assert json.loads((target / "listings.json").read_text(encoding="utf-8"))["listings"][0]["id"] == "old"
    assert (target / "only-old").is_file()
    assert not (target / "only-new").exists()
    assert not report.exists()
    assert not promote_app_candidate._promotion_journal_path(target).exists()


def test_sqlite_snapshot_fails_closed_while_another_process_owns_publication_lock(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    _make_db(source, 2)
    _make_db(target, 1)
    code = (
        "from pathlib import Path; import sys; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "from src.sqlite_atomic import sqlite_publication_lock; "
        f"lock=sqlite_publication_lock(Path({str(target)!r})); lock.__enter__(); "
        "print('LOCKED', flush=True); sys.stdin.readline(); lock.__exit__(None,None,None)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "LOCKED"
        with pytest.raises(SQLitePublicationLocked):
            atomic_sqlite_snapshot(source, target)
        with pytest.raises(SQLitePublicationLocked):
            promote_db_candidate.promote_transaction(
                candidate=source,
                target=target,
                backup=tmp_path / "promotion-backup.sqlite",
                json_out=tmp_path / "promotion.json",
                payload={"ok": True, "dry_run": False},
            )
        with pytest.raises(SQLitePublicationLocked):
            rollback_db_candidate.apply_atomic_rollback(
                source, target, tmp_path / "before-rollback.sqlite"
            )
        assert _db_count(target) == 1
        assert not (tmp_path / "promotion-backup.sqlite").exists()
        assert not (tmp_path / "before-rollback.sqlite").exists()
    finally:
        if process.stdin:
            process.stdin.close()
        process.wait(timeout=10)


def test_pipeline_commit_flags_follow_durable_final_summary() -> None:
    script = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    postflight = script.index("run_step postflight_public_contract")
    final_summary = script.index("final_summary.json")
    app_keep = script.index("APP_KEEP=1")
    db_keep = script.index("DB_PROMOTE_KEEP=1")

    assert postflight < final_summary < app_keep
    assert postflight < final_summary < db_keep
    assert "os.fsync" in script[postflight:app_keep]


def test_injected_final_summary_failure_stays_before_rollback_commit_flags(tmp_path: Path) -> None:
    script = (ROOT / "scripts" / "immo_daily_public_refresh.sh").read_text(encoding="utf-8")
    marker = '"$PY" - "$RUN_DIR" "$PROJECT" <<\'PY\'\n'
    start = script.index(marker) + len(marker)
    end = script.index("\nPY\n", start)
    summary_program = script[start:end]
    run_dir = tmp_path / "run"
    app = tmp_path / "project" / "artifacts" / "app"
    run_dir.mkdir()
    app.mkdir(parents=True)
    # Inject a failure in the first parse performed after postflight.
    (app / "listings.json").write_text("{broken", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-", str(run_dir), str(tmp_path / "project")],
        input=summary_program,
        text=True,
        capture_output=True,
        cwd=ROOT,
    )

    assert result.returncode != 0
    assert not (run_dir / "final_summary.json").exists()
    commit_tail = script[end:]
    assert commit_tail.index("APP_KEEP=1") < commit_tail.index("DB_PROMOTE_KEEP=1")
    pre_summary = script[:start]
    assert "APP_KEEP=1" not in pre_summary
    assert "DB_PROMOTE_KEEP=1" not in pre_summary
    trap = script[script.index("restore_on_failure()") : script.index("trap restore_on_failure EXIT")]
    assert '${APP_KEEP:-0}' in trap
    assert '${DB_PROMOTE_KEEP:-0}' in trap


def test_app_recovery_rejects_unconfined_journal_without_deleting_victim(tmp_path: Path) -> None:
    target = tmp_path / "artifacts" / "app"
    target.parent.mkdir()
    _app(target, "old")
    victim = tmp_path / "victim.txt"
    victim.write_text("do-not-delete", encoding="utf-8")
    journal = promote_app_candidate._promotion_journal_path(target)
    write_journal(
        journal,
        {
            "kind": "app_promotion",
            "op": "promote_app",
            "state": "prepared",
            "target": str(target.resolve()),
            "prepared": str((target.parent / ".app.promote-prepared-123").resolve()),
            "backup": str((target.parent / "app.pre-promote-123").resolve()),
            "json_out": str(victim.resolve()),
            "prepared_report": None,
            "original_names": ["listings.json", "only-old"],
            "prepared_names": ["listings.json", "only-new"],
        },
    )

    with pytest.raises(RuntimeError, match="journal"):
        promote_app_candidate.recover_pending_promotion(target)

    assert victim.read_text(encoding="utf-8") == "do-not-delete"
    assert journal.exists()


def test_db_recovery_rejects_unconfined_exact_copy_without_deleting_victim(tmp_path: Path) -> None:
    target = tmp_path / "data" / "production.sqlite"
    target.parent.mkdir()
    _make_db(target, 3)
    victim = tmp_path / "victim.sqlite"
    _make_db(victim, 99)
    journal = promote_db_candidate._promotion_journal_path(target)
    write_journal(
        journal,
        {
            "kind": "sqlite_promotion",
            "op": "promote_db",
            "state": "prepared",
            "target": str(target.resolve()),
            "backup": str((target.parent / "production.sqlite.pre-promote-123").resolve()),
            "json_out": str((target.parent / "run" / "promote.json").resolve()),
            "prepared_report": None,
            "exact_target": {str(target.resolve()): str(victim.resolve())},
        },
    )

    with pytest.raises(RuntimeError, match="journal"):
        promote_db_candidate.recover_pending_promotion(target)

    assert _db_count(target) == 3
    assert _db_count(victim) == 99
    assert journal.exists()


def test_next_rollback_invocation_recovers_a_killed_partial_child_swap(tmp_path: Path) -> None:
    target = tmp_path / "app"
    backup = tmp_path / "app.pre-promote-123"
    prepared = tmp_path / "app.rollback-prepared-123"
    snapshot = tmp_path / "app.pre-rollback-123"
    report = tmp_path / "rollback.json"
    _app(target, "new")
    _app(backup, "old")
    _app(prepared, "old")
    rollback_public_app._write_pending_rollback_journal(
        target=target,
        backup=backup,
        prepared=prepared,
        snapshot=snapshot,
    )
    snapshot.mkdir()
    os.replace(target / "listings.json", snapshot / "listings.json")
    os.replace(prepared / "listings.json", target / "listings.json")

    restarted = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "rollback_public_app.py"),
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
    )

    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    assert json.loads((target / "listings.json").read_text(encoding="utf-8"))["listings"][0]["id"] == "old"
    assert (target / "only-old").is_file()
    assert not (target / "only-new").exists()
    assert not rollback_public_app._rollback_journal_path(target).exists()


def test_app_rollback_fails_closed_while_another_process_owns_target_lock(tmp_path: Path) -> None:
    target = tmp_path / "app"
    backup = tmp_path / "app.pre-promote-123"
    report = tmp_path / "rollback.json"
    _app(target, "new")
    _app(backup, "old")
    code = (
        "from pathlib import Path; import sys; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "from src.sqlite_atomic import sqlite_publication_lock; "
        f"lock=sqlite_publication_lock(Path({str(target)!r})); lock.__enter__(); "
        "print('LOCKED', flush=True); sys.stdin.readline(); lock.__exit__(None,None,None)"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "rollback_public_app.py"),
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
        )
        assert result.returncode != 0
        assert json.loads((target / "listings.json").read_text(encoding="utf-8"))["listings"][0]["id"] == "new"
    finally:
        if holder.stdin:
            holder.stdin.close()
        holder.wait(timeout=10)
