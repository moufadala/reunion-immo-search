from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_public_delta_guard.py"


def _write(path: Path, name: str, rows: list[dict]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(json.dumps({"listings": rows}), encoding="utf-8")


def _row(source: str, ident: str, *, active: bool = True) -> dict:
    return {
        "id": f"{source}:{ident}",
        "source": source,
        "active": active,
        "commune": "Saint-Denis",
        "type": "Appartement",
        "rent": 1200,
        "surface": 70,
        "images": [f"/{source}-{ident}.jpg"],
    }


def _run(baseline: Path, candidate: Path, tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    report = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--baseline", str(baseline), "--candidate", str(candidate), "--json-out", str(report)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    return result, json.loads(report.read_text(encoding="utf-8"))


def test_inactive_technical_history_does_not_create_false_public_drop(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    public = [_row("seloger", str(i)) for i in range(20)]
    history = [_row("seloger", f"old-{i}", active=False) for i in range(180)]
    _write(baseline, "listings.json", public + history)
    _write(baseline, "feed.json", public)
    _write(candidate, "listings.json", public)

    result, report = _run(baseline, candidate, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["baseline_count"] == report["candidate_count"] == 20
    assert report["product_boundary"] == "active_public_policy_dedup"


def test_true_critical_source_collapse_still_blocks(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    baseline_rows = [_row("seloger", str(i)) for i in range(20)] + [_row("ofim", str(i)) for i in range(80)]
    candidate_rows = [_row("seloger", str(i)) for i in range(5)] + [_row("ofim", str(i)) for i in range(80)]
    _write(baseline, "feed.json", baseline_rows)
    _write(candidate, "listings.json", candidate_rows)

    result, report = _run(baseline, candidate, tmp_path)

    assert result.returncode == 2
    assert report["critical_sources"][0]["baseline"] == 20
    assert report["critical_sources"][0]["candidate"] == 5
    assert any("critical source seloger dropped" in error for error in report["errors"])


def test_candidate_technical_photo_and_price_fields_are_deduplicated_like_feed(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    canonical = _row("seloger", "same") | {
        "address": "1 rue Test",
        "images": ["/same.jpg"],
        "description": "Référence annonce: GES10980017-495",
    }
    duplicate = {
        "id": "ofim:same",
        "source": "ofim",
        "active": True,
        "city": "Saint-Denis",
        "type": "Appartement",
        "price": 1200,
        "surface": 70,
        "address": "1 rue Test",
        "local_image_urls": ["/same.jpg"],
        "description": "Référence annonce: GES10980017-495",
    }
    _write(baseline, "feed.json", [canonical])
    _write(candidate, "listings.json", [canonical, duplicate])

    result, report = _run(baseline, candidate, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["baseline_count"] == report["candidate_count"] == 1
    assert report["candidate_dedup"]["hidden_duplicates"] == 1
