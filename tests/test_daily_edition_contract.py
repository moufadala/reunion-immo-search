from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_daily_edition.py"

spec = importlib.util.spec_from_file_location("build_daily_edition", SCRIPT)
assert spec is not None and spec.loader is not None
daily_edition = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = daily_edition
spec.loader.exec_module(daily_edition)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_failed_immo_refresh_still_publishes_degraded_daily_status(tmp_path: Path) -> None:
    run_dir = tmp_path / "reunion-watch" / "20260825T163033Z_daily"
    public_app = tmp_path / "public-app"
    (run_dir / "immo_public_refresh").mkdir(parents=True)
    (run_dir / "dashboard.html").write_text("<html>15 annonces</html>", encoding="utf-8")
    (run_dir / "immo_public_refresh" / "realestate_refresh.status").write_text(
        "realestate_refresh rc=1 duration_s=781\n",
        encoding="utf-8",
    )
    (run_dir / "immo_public_refresh" / "reunion_watch.stage.db").write_bytes(b"sqlite")
    write_json(
        run_dir / "manifest.json",
        {
            "ok": True,
            "generated_at": "2026-08-25T16:30:33+00:00",
            "immo_summary": {
                "ok": False,
                "selected_count": 15,
                "sources": {"bienici": 30, "zimo": 56},
                "source_gate": {"ok": False, "ok_count": 11, "total": 13},
                "failed_sources": ["leboncoin", "superimmo"],
                "failed_source_details": [
                    {"source": "leboncoin", "motif": "HTTP 403"},
                    {"source": "superimmo", "motif": "timeout"},
                ],
            },
        },
    )
    write_json(run_dir / "pipeline_result.json", {"ok": True, "run_dir": str(run_dir)})

    result = daily_edition.build_daily_edition(
        run_dir=run_dir,
        public_app=public_app,
        edition_root=tmp_path / "editions",
        immo_refresh_exit_code=1,
        public_base_url="https://immo.example.test/",
    )

    assert result["status"] == "degraded"
    assert result["published"]["status_page"] is True
    assert result["public_feed_refreshed"] is False
    assert "leboncoin" in result["failed_sources"]
    assert (public_app / "daily-status" / "index.html").exists()
    assert (public_app / "daily-status" / "edition_manifest.json").exists()
    assert (public_app / "daily-status" / "dashboard.html").exists()
    telegram = (run_dir / "telegram_summary.txt").read_text(encoding="utf-8")
    assert "IMMO SOIR - PUBLIE DEGRADE" in telegram
    assert "Flux public rafraichi: non" in telegram
    pipeline = json.loads((run_dir / "pipeline_result.json").read_text(encoding="utf-8"))
    assert pipeline["ok"] is False
    assert pipeline["immo_public_refresh_exit_code"] == 1
    assert pipeline["daily_edition"]["status"] == "degraded"


def test_publication_canary_alerts_when_today_status_is_missing(tmp_path: Path) -> None:
    app = tmp_path / "public-app"
    app.mkdir()

    ok, message = daily_edition.check_daily_publication(
        public_app=app,
        expected_date="2026-08-27",
        public_base_url="https://immo.example.test/",
    )

    assert ok is False
    assert "ALERTE IMMO" in message
    assert "publication quotidienne absente" in message


def test_publication_canary_is_silent_for_today_degraded_status(tmp_path: Path) -> None:
    app = tmp_path / "public-app"
    write_json(
        app / "daily-status" / "edition_manifest.json",
        {
            "date": "2026-08-27",
            "status": "degraded",
            "published": {"status_page": True},
            "public_status_url": "https://immo.example.test/daily-status/",
        },
    )

    ok, message = daily_edition.check_daily_publication(
        public_app=app,
        expected_date="2026-08-27",
        public_base_url="https://immo.example.test/",
    )

    assert ok is True
    assert message == ""


def test_canary_wrapper_defaults_to_publication_check(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["IMMO_PUBLIC_APP"] = str(tmp_path / "missing-public-app")
    env["IMMO_PUBLIC_BASE_URL"] = "https://immo.example.test/"

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "immo_daily_publication_canary.py")],
        cwd=ROOT,
        env=env,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "ALERTE IMMO" in result.stdout
    assert "publication quotidienne absente" in result.stdout
    assert "--run-dir is required" not in result.stderr
