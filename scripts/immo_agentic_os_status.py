#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def parse_step_status(text: str) -> dict[str, Any]:
    name = text.split()[0] if text.strip() else "unknown"
    out: dict[str, Any] = {"name": name, "raw": text.strip()}
    for key, value in re.findall(r"(\w+)=([^\s]+)", text):
        if key == "rc":
            try:
                out[key] = int(value)
            except ValueError:
                out[key] = value
        else:
            out[key] = value
    out["ok"] = out.get("rc") == 0
    out["resumed"] = "skipped=resume_completed" in text
    return out


def step_statuses(run_dir: Path) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("*.status")):
        statuses.append(parse_step_status(path.read_text(encoding="utf-8", errors="replace")))
    for path in sorted(run_dir.glob("*.resume.status")):
        parsed = parse_step_status(path.read_text(encoding="utf-8", errors="replace"))
        parsed["resume_marker"] = True
        statuses.append(parsed)
    return statuses


def feed_summary(app: Path) -> dict[str, Any]:
    feed = load_json(app / "feed.json", {})
    rows = feed.get("listings") if isinstance(feed, dict) else []
    if not isinstance(rows, list):
        rows = []
    active = [r for r in rows if isinstance(r, dict) and r.get("active", True)]
    by_source: dict[str, int] = {}
    missing_photo = 0
    bad_desc = 0
    for row in active:
        src = str(row.get("source") or row.get("source_site") or "unknown").lower()
        by_source[src] = by_source.get(src, 0) + 1
        if not row.get("image"):
            missing_photo += 1
        desc = str(row.get("description") or row.get("summary") or "").strip()
        if len(desc) < 25:
            bad_desc += 1
    raw_meta = feed.get("meta") if isinstance(feed, dict) else None
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    return {
        "exists": (app / "feed.json").exists(),
        "active_count": len(active),
        "by_source": by_source,
        "missing_photo": missing_photo,
        "bad_description": bad_desc,
        "generated_at": meta.get("genere_le"),
    }


def source_tickets(manifest_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    tickets: list[dict[str, Any]] = []
    sources = manifest_bundle.get("sources") if isinstance(manifest_bundle, dict) else []
    if not isinstance(sources, list):
        return tickets
    for item in sources:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "unknown")
        status = str(item.get("status") or "unknown")
        raw_signals = item.get("truncation_signals")
        signals: list[Any] = raw_signals if isinstance(raw_signals, list) else []
        error = str(item.get("error") or "")
        normalized = int(item.get("normalized_items") or item.get("unique_ids") or 0)
        if status != "complete":
            if source == "superimmo" or "captcha" in error.lower() or "429" in error or "503" in error:
                action = "cooldown_then_retry_source_or_page; consider human HAR if repeated"
                severity = "important_non_blocking"
            else:
                action = "retry_source_only_with_same_run_dir"
                severity = "important_non_blocking"
            tickets.append({
                "id": f"source-{source}-not-complete",
                "kind": "source",
                "source": source,
                "severity": severity,
                "status": status,
                "reason": error or ",".join(map(str, signals)) or "source_not_complete",
                "impact": "source isolated; publication can continue if public gates pass",
                "next_action": action,
            })
        elif source == "seloger" and normalized > 0:
            # SeLoger often collects many rows but only a few remain publishable after content gates.
            tickets.append({
                "id": "seloger-content-quality",
                "kind": "enrichment",
                "source": "seloger",
                "severity": "watch",
                "status": "complete_source_snapshot",
                "reason": f"source_normalized={normalized}; verify published/photo/description ratio",
                "impact": "quality chantier, not global run blocker",
                "next_action": "enrich_missing_seloger_descriptions_and_local_photos_only",
            })
    return tickets


def load_first_json(run_dir: Path, names: list[str], pattern: str | None = None, *, recursive: bool = False) -> Any:
    candidates: list[Any] = []
    for name in names:
        data = load_json(run_dir / name, None)
        if isinstance(data, dict):
            candidates.append(data)
    if pattern:
        iterator = run_dir.rglob(pattern) if recursive else run_dir.glob(pattern)
        for path in sorted(iterator):
            data = load_json(path, None)
            if isinstance(data, dict):
                candidates.append(data)
    for data in reversed(candidates):
        if data.get("ok") is True:
            return data
    return candidates[-1] if candidates else {}


def json_gate_ok(payload: Any, fallback_exists: bool) -> bool:
    if not isinstance(payload, dict):
        return False
    if "ok" in payload:
        return bool(payload.get("ok"))
    checks = payload.get("checks")
    if isinstance(checks, list):
        blocking = [c for c in checks if isinstance(c, dict) and c.get("ok") is False and c.get("severity", "blocking") == "blocking"]
        return not blocking
    return fallback_exists


def resume_smoke_ok(run_dir: Path) -> bool:
    for path in run_dir.rglob("resume_smoke_summary.json"):
        data = load_json(path, {})
        if isinstance(data, dict) and data.get("ok") is True:
            return True
    return False


def build_status(run_dir: Path, app: Path) -> dict[str, Any]:
    steps = step_statuses(run_dir)
    failed_steps = [s for s in steps if s.get("rc") not in (0, None)]
    resumed_steps = [s for s in steps if s.get("resumed")]
    postflight = load_first_json(run_dir, ["postflight_public_contract.json", "73_postflight_final.json"], "*postflight*.json")
    qa = load_first_json(run_dir, ["qa_public_v2_final.json", "74_qa_public_v2_final.json"], "*qa_public_v2*.json")
    manifests = load_first_json(run_dir, ["source_run_manifests.json"], "source_run_manifests.json", recursive=True)
    feed = feed_summary(app)
    tickets = source_tickets(manifests)

    if failed_steps:
        tickets.append({
            "id": "pipeline-step-failed",
            "kind": "step",
            "severity": "critical",
            "reason": ", ".join(str(s.get("name")) for s in failed_steps),
            "impact": "publication/release cannot be marked DONE until isolated or fixed",
            "next_action": "resume from failed step with IMMO_RESUME_COMPLETED=1, do not full rerun",
        })
    if not feed["exists"] or feed["active_count"] < 100:
        tickets.append({
            "id": "public-feed-volume",
            "kind": "publication",
            "severity": "critical",
            "reason": f"feed_exists={feed['exists']} active_count={feed['active_count']}",
            "impact": "dashboard not functionally usable",
            "next_action": "rollback_to_last_good_feed_or_publish_stage_candidate_after_QA",
        })

    postflight_exists = any((run_dir / name).exists() for name in ("postflight_public_contract.json", "73_postflight_final.json")) or bool(list(run_dir.glob("*postflight*.json")))
    qa_exists = any((run_dir / name).exists() for name in ("qa_public_v2_final.json", "74_qa_public_v2_final.json")) or bool(list(run_dir.glob("*qa_public_v2*.json")))
    postflight_ok = json_gate_ok(postflight, postflight_exists)
    qa_ok = json_gate_ok(qa, qa_exists)
    if not postflight_ok:
        tickets.append({
            "id": "postflight-gate-missing-or-failed",
            "kind": "publication_gate",
            "severity": "critical",
            "reason": "postflight_public_contract not found or not OK",
            "impact": "cannot mark publication verified",
            "next_action": "run postflight_public_contract only; do not full rerun",
        })
    if not qa_ok:
        tickets.append({
            "id": "qa-public-v2-missing-or-failed",
            "kind": "publication_gate",
            "severity": "critical",
            "reason": "qa_public_v2 not found or not OK",
            "impact": "cannot mark public app verified",
            "next_action": "run qa_public_v2 only; do not full rerun",
        })
    blocking = [t for t in tickets if t.get("severity") == "critical"]
    functional = not blocking and feed["active_count"] >= 100 and postflight_ok and qa_ok
    resume_evidence = bool(resumed_steps) or resume_smoke_ok(run_dir) or any("resume" in str(s.get("name", "")) for s in steps)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "app": str(app),
        "etat_metier": "FONCTIONNEL" if functional else "BLOQUE",
        "done_contract": {
            "publication_functional": feed["active_count"] >= 100 and postflight_ok and qa_ok,
            "agentic_resume_evidence": resume_evidence,
            "degraded_sources_isolated": not blocking,
            "human_machine_report": True,
        },
        "steps": {"total": len(steps), "failed": failed_steps, "resumed": resumed_steps},
        "feed": feed,
        "tickets": tickets,
        "next_actions": [t["next_action"] for t in tickets[:8]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build immo agentic OS status/tickets from a run directory and public app.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--app", default="/opt/data/projects/reunion-immo-search/artifacts/app")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    status = build_status(Path(args.run_dir), Path(args.app))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(json.dumps({"ok": status["etat_metier"] == "FONCTIONNEL", "etat_metier": status["etat_metier"], "tickets": len(status["tickets"]), "out": str(out)}, ensure_ascii=False))
    return 0 if status["etat_metier"] == "FONCTIONNEL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
