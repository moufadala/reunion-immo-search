from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pipeline_publication_transaction.py"


def _db(path: Path, marker: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE state(marker TEXT NOT NULL)")
        connection.execute("INSERT INTO state VALUES (?)", (marker,))
        connection.commit()


def _db_marker(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return str(connection.execute("SELECT marker FROM state").fetchone()[0])


def _replace_db(path: Path, marker: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE state SET marker=?", (marker,))
        connection.commit()


def _app(path: Path, marker: str, *, full: bool = True) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "listings.json").write_text(
        json.dumps({"listings": [{"id": marker}]}), encoding="utf-8"
    )
    if full:
        (path / "index.html").write_text(marker, encoding="utf-8")
        (path / "feed.json").write_text(
            json.dumps({"listings": [{"id": marker}]}), encoding="utf-8"
        )


def _run(operation: str, journal: Path, db: Path, app: Path, history: Path, *, run_id: str = "run-test"):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            operation,
            "--journal",
            str(journal),
            "--run-id",
            run_id,
            "--db-target",
            str(db),
            "--app-target",
            str(app),
            "--history-target",
            str(history),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize("kill_point", ["headless_app", "all_promoted"])
def test_next_official_run_recovers_global_transaction_after_kill(
    tmp_path: Path, kill_point: str
) -> None:
    data = tmp_path / "data"
    artifacts = tmp_path / "project" / "artifacts"
    db = data / "production.sqlite"
    history = data / "history.sqlite"
    app = artifacts / "app"
    journal = artifacts / ".daily-public-transaction.json"
    data.mkdir()
    artifacts.mkdir(parents=True)
    _db(db, "old-db")
    _db(history, "old-history")
    _app(app, "old-app")

    begun = _run("begin", journal, db, app, history)
    assert begun.returncode == 0, begun.stdout + begun.stderr
    assert journal.exists()
    _replace_db(db, "new-db")
    if kill_point == "headless_app":
        for child in list(app.iterdir()):
            if child.name != "listings.json":
                child.unlink()
        (app / "listings.json").write_text(
            json.dumps({"listings": [{"id": "headless"}]}), encoding="utf-8"
        )
    else:
        for child in list(app.iterdir()):
            child.unlink()
        _app_contents = {"listings.json": {"listings": [{"id": "new-app"}]}}
        (app / "listings.json").write_text(json.dumps(_app_contents["listings.json"]), encoding="utf-8")
        (app / "index.html").write_text("new-app", encoding="utf-8")
        (app / "feed.json").write_text(json.dumps(_app_contents["listings.json"]), encoding="utf-8")
        _replace_db(history, "new-history")

    recovered = _run("recover", journal, db, app, history, run_id="next-official-run")

    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert _db_marker(db) == "old-db"
    assert _db_marker(history) == "old-history"
    assert (app / "index.html").read_text(encoding="utf-8") == "old-app"
    assert not journal.exists()
    assert json.loads((app / "feed.json").read_text(encoding="utf-8"))["listings"][0]["id"] == "old-app"


def test_corrupt_global_journal_cannot_delete_outside_backup(tmp_path: Path) -> None:
    data = tmp_path / "data"
    artifacts = tmp_path / "project" / "artifacts"
    db, history, app = data / "production.sqlite", data / "history.sqlite", artifacts / "app"
    journal = artifacts / ".daily-public-transaction.json"
    data.mkdir()
    artifacts.mkdir(parents=True)
    _db(db, "old-db")
    _db(history, "old-history")
    _app(app, "old-app")
    assert _run("begin", journal, db, app, history).returncode == 0
    victim = tmp_path / "victim.txt"
    victim.write_text("must survive", encoding="utf-8")
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["db_backup"] = str(victim.resolve())
    journal.write_text(json.dumps(payload), encoding="utf-8")

    recovered = _run("recover", journal, db, app, history)

    assert recovered.returncode != 0
    assert victim.read_text(encoding="utf-8") == "must survive"
    assert journal.exists()


def test_committed_global_transaction_is_not_rolled_back(tmp_path: Path) -> None:
    data = tmp_path / "data"
    artifacts = tmp_path / "project" / "artifacts"
    db, history, app = data / "production.sqlite", data / "history.sqlite", artifacts / "app"
    journal = artifacts / ".daily-public-transaction.json"
    data.mkdir()
    artifacts.mkdir(parents=True)
    _db(db, "old-db")
    _db(history, "old-history")
    _app(app, "old-app")
    assert _run("begin", journal, db, app, history).returncode == 0
    _replace_db(db, "committed-db")
    committed = _run("commit", journal, db, app, history)
    assert committed.returncode == 0, committed.stdout + committed.stderr

    assert _run("recover", journal, db, app, history).returncode == 0
    assert _db_marker(db) == "committed-db"
    assert not journal.exists()
