#!/usr/bin/env python3
"""Cron-safe Telegram digest for immo source health changes.

Reads source_health.json and emits stdout only when a critical source newly
needs attention or recovers. Persistent state prevents daily repeated spam.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_HEALTH = ROOT / "artifacts" / "app" / "source_health.json"
DEFAULT_STATE = Path("/opt/data/artifacts/immo-alerts/source_health_seen.json")
ATTENTION_STATUSES = {"aging", "stale", "unknown", "empty"}
ATTENTION_SEVERITIES = {"high", "medium", "warning"}


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


def source_needs_attention(src: dict[str, Any]) -> bool:
    return bool(src.get("is_critical")) and (
        src.get("status") in ATTENTION_STATUSES or src.get("severity") in ATTENTION_SEVERITIES
    ) and src.get("severity") != "ok"


def status_key(src: dict[str, Any]) -> str:
    return f"{src.get('status')}|{src.get('severity')}|{src.get('reason')}"


def compact_line(src: dict[str, Any]) -> str:
    source = src.get("source") or "source"
    status = src.get("status") or "?"
    reason = src.get("reason") or "raison n.c."
    active = src.get("active_rows", 0)
    smoke = src.get("smoke_status") or "smoke n.c."
    return f"- {source}: {status} · {reason} · {active} active(s) · {smoke}"


def build_alert(payload: dict[str, Any], state: dict[str, Any], cooldown_hours: int, limit: int) -> tuple[str, dict[str, Any]]:
    now = now_utc()
    state.setdefault("sources", {})
    source_state: dict[str, Any] = state["sources"]
    new_bad: list[dict[str, Any]] = []
    recovered: list[dict[str, Any]] = []
    repeated: list[dict[str, Any]] = []

    for src in payload.get("sources", []) or []:
        name = str(src.get("source") or "unknown")
        prev = source_state.get(name) or {}
        prev_bad = bool(prev.get("attention"))
        current_bad = source_needs_attention(src)
        key = status_key(src)
        prev_key = prev.get("status_key")
        last_alerted = parse_dt(prev.get("last_alerted_at"))
        cooldown_ok = not last_alerted or now - last_alerted >= timedelta(hours=cooldown_hours)

        if current_bad:
            if not prev_bad or key != prev_key:
                new_bad.append(src)
                prev["last_alerted_at"] = now.isoformat()
            elif cooldown_ok:
                repeated.append(src)
                prev["last_alerted_at"] = now.isoformat()
        elif prev_bad:
            recovered.append(src)
            prev["last_alerted_at"] = now.isoformat()

        prev.update({
            "attention": current_bad,
            "status": src.get("status"),
            "severity": src.get("severity"),
            "status_key": key,
            "last_seen_payload_at": payload.get("generated_at"),
            "updated_at": now.isoformat(),
        })
        source_state[name] = prev

    state["updated_at"] = now.isoformat()
    parts: list[str] = []
    if new_bad:
        parts.append("⚠️ Immo Réunion — source(s) à vérifier")
        parts.extend(compact_line(s) for s in new_bad[:limit])
        if len(new_bad) > limit:
            parts.append(f"… +{len(new_bad)-limit} autre(s)")
    if recovered:
        if parts:
            parts.append("")
        parts.append("✅ Source(s) revenues OK")
        parts.extend(compact_line(s) for s in recovered[:limit])
    # Repeated alerts are intentionally quiet by default unless every source is still bad
    # after cooldown and there is no newer signal. This keeps watchdogs visible but low-noise.
    if repeated and not new_bad and not recovered:
        parts.append("⚠️ Immo Réunion — source(s) toujours en anomalie")
        parts.extend(compact_line(s) for s in repeated[:limit])
    if parts:
        parts.append("")
        parts.append("Détails: https://immo.148.230.103.174.sslip.io/source_health.html")
    return ("\n".join(parts).strip() + "\n") if parts else "", state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-health", default=str(DEFAULT_SOURCE_HEALTH))
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--cooldown-hours", type=int, default=24)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    source_path = Path(args.source_health)
    state_path = Path(args.state)
    payload = load_json(source_path, {})
    if not payload.get("ok"):
        print(f"⚠️ Immo Réunion — source_health.json illisible ou absent: {source_path}")
        return 2
    state = load_json(state_path, {"version": 1, "sources": {}})
    message, new_state = build_alert(payload, state, args.cooldown_hours, args.limit)
    if args.dry_run:
        bad = [s for s in payload.get("sources", []) or [] if source_needs_attention(s)]
        print(json.dumps({"ok": True, "dry_run": True, "attention_count": len(bad), "would_emit": bool(message), "message": message}, ensure_ascii=False, indent=2))
        return 0
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")
    if message:
        print(message, end="")
    elif args.verbose:
        print(json.dumps({"ok": True, "new_source_alerts": 0}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
