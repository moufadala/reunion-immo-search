#!/usr/bin/env python3
"""Cron-safe Telegram alerts for immo source health.

Modes:
- changes: emit only state transitions (source newly needs attention, source recovers,
  or status class changes). No repeated persistence spam.
- digest: emit one daily summary for sources still needing attention.

Empty stdout means silent OK for Hermes cron/no_agent jobs.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_HEALTH = ROOT / "artifacts" / "app" / "source_health.json"
DEFAULT_STATE = Path("/opt/data/artifacts/immo-alerts/source_health_seen.json")
DB_PROOF_PATH = "/opt/data/data/reunion_watch.db"
ATTENTION_STATUSES = {"aging", "stale", "unknown", "empty"}
ATTENTION_SEVERITIES = {"high", "medium", "warning"}
MAX_RECENT_HOURS = 36.0


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def measure_datetime(src: dict[str, Any]) -> datetime | None:
    return parse_dt(src.get("last_seen_at") or src.get("last_fetched_at"))


def measure_age_hours(src: dict[str, Any], *, reference: datetime | None = None) -> float | None:
    dt = measure_datetime(src)
    if dt:
        now = reference or now_utc()
        return max(0.0, (now - dt).total_seconds() / 3600)
    raw_age = src.get("age_hours")
    if raw_age is None:
        return None
    try:
        return float(raw_age)
    except (TypeError, ValueError):
        return None


def measure_is_recent(src: dict[str, Any], *, reference: datetime | None = None) -> bool:
    age = measure_age_hours(src, reference=reference)
    return age is not None and age <= MAX_RECENT_HOURS


def source_needs_attention(src: dict[str, Any]) -> bool:
    if not bool(src.get("is_critical")):
        return False
    semantic_bad = (
        src.get("status") in ATTENTION_STATUSES or src.get("severity") in ATTENTION_SEVERITIES
    ) and src.get("severity") != "ok"
    # Guard against contradictory payloads: a source cannot be declared recovered
    # if its own evidence says the last data is still older than the freshness SLA.
    measurement_bad = src.get("severity") == "ok" and not measure_is_recent(src)
    return bool(semantic_bad or measurement_bad)


def status_key(src: dict[str, Any]) -> str:
    """Semantic state key. Do not include reason/age: it drifts every run and would spam."""
    base = f"{src.get('status')}|{src.get('severity')}"
    if src.get("severity") == "ok" and not measure_is_recent(src):
        return f"inconsistent_measure|{base}"
    return base


def source_label(src: dict[str, Any]) -> str:
    name = str(src.get("source") or "source").strip()
    return name[:1].upper() + name[1:]


def hours_to_human(hours: Any) -> str:
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return "durée inconnue"
    if h < 1:
        return f"{int(round(h * 60))} min"
    if h < 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} j"


def proof_command(src: dict[str, Any]) -> str:
    source = str(src.get("source") or "").replace("'", "''")
    return (
        f"sqlite3 {DB_PROOF_PATH} \"select count(*),max(seen_last_at) "
        f"from rental_listings where source_site='{source}' and coalesce(is_active,1)=1\""
    )


def measure_line(src: dict[str, Any], *, recovered: bool = False, prev: dict[str, Any] | None = None) -> str:
    active = int(src.get("active_rows") or 0)
    last_seen = src.get("last_seen_at") or src.get("last_fetched_at") or "n.c."
    age = measure_age_hours(src)
    if recovered:
        previous_since = parse_dt((prev or {}).get("first_attention_at"))
        last_seen_dt = measure_datetime(src)
        outage_start = previous_since
        if last_seen_dt and (outage_start is None or outage_start > last_seen_dt):
            outage_start = last_seen_dt
        if outage_start:
            elapsed_h = max(0.0, (now_utc() - outage_start).total_seconds() / 3600)
            return f"{active} active(s) · après {hours_to_human(elapsed_h)} d'arrêt · mesure : seen_last_at={last_seen}"
        return f"{active} active(s) · mesure : seen_last_at={last_seen}"
    return f"Depuis : {hours_to_human(age)} · mesure : {active} active(s), seen_last_at={last_seen}"


def why_line(src: dict[str, Any], *, recovered: bool = False) -> str:
    name = str(src.get("source") or "source")
    if recovered:
        return f"Pourquoi : {name} réalimente le feed public"
    if src.get("severity") == "ok" and not measure_is_recent(src):
        return f"Pourquoi : statut annoncé OK, mais dernière donnée > {MAX_RECENT_HOURS:g} h — incohérence à vérifier"
    status = src.get("status") or "anomalie"
    return f"Pourquoi : source {status}; si ça dure, le feed public vieillit ou perd une partie du parc"


def action_line(*, recovered: bool = False, digest: bool = False) -> str:
    if recovered:
        return "Qui agit : personne · résolu"
    if digest:
        return "Qui agit : Hermès · suivi en digest, pas d'action Moufadal immédiate"
    return "Qui agit : Hermès · vérifier/réparer la source si la transition est inattendue"


def format_block(src: dict[str, Any], *, kind: str, prev: dict[str, Any] | None = None) -> str:
    name = source_label(src)
    if kind == "recovered":
        title = f"🟢 IMMO · {name} est revenue"
        return "\n".join([
            title,
            measure_line(src, recovered=True, prev=prev),
            why_line(src, recovered=True),
            action_line(recovered=True),
            f"Preuve : {proof_command(src)}",
        ])
    title = f"⚠️ IMMO · {name} est à vérifier"
    if kind == "digest":
        title = f"🟡 IMMO · {name} reste à vérifier"
    return "\n".join([
        title,
        measure_line(src),
        why_line(src),
        action_line(digest=(kind == "digest")),
        f"Preuve : {proof_command(src)}",
    ])


def build_alert(payload: dict[str, Any], state: dict[str, Any], mode: str = "changes", limit: int = 8) -> tuple[str, dict[str, Any]]:
    now = now_utc()
    state.setdefault("version", 2)
    state.setdefault("sources", {})
    source_state: dict[str, Any] = state["sources"]
    changed_bad: list[tuple[dict[str, Any], dict[str, Any]]] = []
    recovered: list[tuple[dict[str, Any], dict[str, Any]]] = []
    persistent_bad: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for src in payload.get("sources", []) or []:
        name = str(src.get("source") or "unknown")
        prev = source_state.get(name) or {}
        prev_bad = bool(prev.get("attention"))
        current_bad = source_needs_attention(src)
        key = status_key(src)
        prev_key = prev.get("status_key")

        if current_bad:
            problem_start = measure_datetime(src)
            problem_start_iso = problem_start.isoformat() if problem_start else now.isoformat()
            existing_start = parse_dt(prev.get("first_attention_at"))
            if existing_start is None or (problem_start and existing_start > problem_start):
                prev["first_attention_at"] = problem_start_iso
            if not prev_bad or key != prev_key:
                changed_bad.append((src, dict(prev)))
                prev["last_transition_at"] = now.isoformat()
            else:
                persistent_bad.append((src, dict(prev)))
        elif prev_bad:
            recovered.append((src, dict(prev)))
            prev["last_transition_at"] = now.isoformat()
            prev.pop("first_attention_at", None)

        prev.update({
            "attention": current_bad,
            "status": src.get("status"),
            "severity": src.get("severity"),
            "status_key": key,
            "last_seen_payload_at": payload.get("generated_at"),
            "updated_at": now.isoformat(),
        })
        if current_bad and not prev.get("first_attention_at"):
            problem_start = measure_datetime(src)
            prev["first_attention_at"] = (problem_start or now).isoformat()
        source_state[name] = prev

    state["updated_at"] = now.isoformat()
    blocks: list[str] = []
    if mode == "changes":
        blocks.extend(format_block(s, kind="changed", prev=p) for s, p in changed_bad[:limit])
        blocks.extend(format_block(s, kind="recovered", prev=p) for s, p in recovered[:limit])
    elif mode == "digest":
        today = now.date().isoformat()
        if state.get("last_digest_date") != today:
            blocks.extend(format_block(s, kind="digest", prev=p) for s, p in (changed_bad + persistent_bad)[:limit])
            if blocks:
                state["last_digest_date"] = today
                state["last_digest_at"] = now.isoformat()
    else:
        raise ValueError(f"unknown mode: {mode}")

    if blocks:
        return "\n\n".join(blocks).strip() + "\n", state
    return "", state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-health", default=str(DEFAULT_SOURCE_HEALTH))
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--mode", choices=["changes", "digest"], default="changes")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    source_path = Path(args.source_health)
    state_path = Path(args.state)
    payload = load_json(source_path, {})
    if not payload.get("ok"):
        print(f"⚠️ IMMO · source_health.json illisible\nDepuis : maintenant · mesure : {source_path}\nPourquoi : l'état des sources ne peut plus être évalué\nQui agit : Hermès · réparer l'export source_health\nPreuve : python3 /opt/data/projects/reunion-immo-search/src/source_health.py")
        return 2
    state = load_json(state_path, {"version": 2, "sources": {}})
    message, new_state = build_alert(payload, state, mode=args.mode, limit=args.limit)
    if args.dry_run:
        bad = [s for s in payload.get("sources", []) or [] if source_needs_attention(s)]
        print(json.dumps({"ok": True, "dry_run": True, "mode": args.mode, "attention_count": len(bad), "would_emit": bool(message), "message": message}, ensure_ascii=False, indent=2))
        return 0
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")
    if message:
        print(message, end="")
    elif args.verbose:
        print(json.dumps({"ok": True, "mode": args.mode, "emitted": 0}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
