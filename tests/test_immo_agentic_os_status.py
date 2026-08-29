from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "immo_agentic_os_status.py"
spec = importlib.util.spec_from_file_location("immo_agentic_os_status_mod", MOD_PATH)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def write_feed(app: Path, count: int = 120) -> None:
    app.mkdir(parents=True)
    rows = [
        {
            "id": f"id-{i}",
            "active": True,
            "source": "zimo" if i < count - 3 else "seloger",
            "image": f"/thumbs/{i}.jpg",
            "description": "description exploitable pour le contrat public",
        }
        for i in range(count)
    ]
    (app / "feed.json").write_text(json.dumps({"meta": {"genere_le": "2026-08-29T00:00:00Z"}, "listings": rows}), encoding="utf-8")


def test_agentic_os_marks_functional_with_public_gates_and_isolated_source_ticket(tmp_path):
    run = tmp_path / "run"
    app = tmp_path / "app"
    run.mkdir()
    write_feed(app)
    (run / "qa_public_v2_final.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (run / "postflight_public_contract.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (run / "source_run_manifests.json").write_text(json.dumps({
        "run_id": "r1",
        "sources": [
            {"source": "superimmo", "status": "failed", "error": "429 hCaptcha", "normalized_items": 0},
            {"source": "seloger", "status": "complete", "normalized_items": 247},
        ],
    }), encoding="utf-8")

    status = mod.build_status(run, app)
    assert status["etat_metier"] == "FONCTIONNEL"
    assert any(t["id"] == "source-superimmo-not-complete" for t in status["tickets"])
    assert any(t["id"] == "seloger-content-quality" for t in status["tickets"])
    assert all(t["severity"] != "critical" for t in status["tickets"])


def test_agentic_os_blocks_when_feed_missing_or_too_small(tmp_path):
    run = tmp_path / "run"
    app = tmp_path / "app"
    run.mkdir()
    write_feed(app, count=5)
    (run / "qa_public_v2_final.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (run / "postflight_public_contract.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    status = mod.build_status(run, app)
    assert status["etat_metier"] == "BLOQUE"
    assert any(t["id"] == "public-feed-volume" and t["severity"] == "critical" for t in status["tickets"])


def test_agentic_os_detects_resume_markers(tmp_path):
    run = tmp_path / "run"
    app = tmp_path / "app"
    run.mkdir()
    write_feed(app)
    (run / "qa_public_v2_final.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (run / "postflight_public_contract.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (run / "build.resume.status").write_text("build rc=0 duration_s=0 skipped=resume_completed", encoding="utf-8")
    status = mod.build_status(run, app)
    assert status["done_contract"]["agentic_resume_evidence"] is True
    assert status["steps"]["resumed"]
