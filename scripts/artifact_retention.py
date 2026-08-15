#!/usr/bin/env python3
"""Phase E P0: bounded artifact retention with hard safety guards.

Deletes only explicitly allowed runtime artifact patterns under the configured
artifacts root. The default mode is dry-run; use --apply for real deletion.
"""
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS = ROOT / "artifacts"

# Hard-coded protected names/segments. These are enforced in code, not comments.
PROTECTED_NAMES = {
    "app",
    "state.db",
    ".git",
    "src",
    "vault",
    "vaults",
    "moufadal-second-brain",
}
ALLOWED_STAGE_PREFIXES = ("daily-clean-stage-", "daily-tech-stage-", "app.pre-promote-", "app.pre-rollback-")


@dataclass(frozen=True)
class Candidate:
    path: Path
    reason: str
    size_bytes: int


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def path_size(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() or child.is_symlink():
                total += child.lstat().st_size
        except OSError:
            continue
    return total


def newest_key(path: Path) -> tuple[float, str]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (mtime, path.name)


def assert_safe_candidate(path: Path, artifacts_root: Path) -> None:
    resolved_root = artifacts_root.resolve()
    resolved = path.resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise ValueError(f"refuse path outside artifacts root: {path}")
    rel_parts = path.relative_to(artifacts_root).parts
    if any(part in PROTECTED_NAMES for part in rel_parts):
        raise ValueError(f"refuse protected path: {path}")
    name = path.name
    allowed = name.startswith(ALLOWED_STAGE_PREFIXES) or name.startswith(".bak.")
    if not allowed:
        raise ValueError(f"refuse non-whitelisted artifact path: {path}")


def retention_candidates(
    artifacts_root: Path,
    *,
    keep_daily: int = 1,
    keep_pre_promote: int = 1,
    protected_paths: set[Path] | None = None,
) -> list[Candidate]:
    if keep_daily < 0 or keep_pre_promote < 0:
        raise ValueError("keep counts must be >= 0")
    protected_paths = {p.resolve() for p in (protected_paths or set())}
    candidates: list[Candidate] = []
    for prefix, keep in (("daily-clean-stage-", keep_daily), ("daily-tech-stage-", keep_daily), ("app.pre-promote-", keep_pre_promote), ("app.pre-rollback-", keep_pre_promote)):
        items = [p for p in artifacts_root.iterdir() if p.name.startswith(prefix)] if artifacts_root.exists() else []
        items = sorted(items, key=newest_key, reverse=True)
        kept = 0
        for old in items:
            if old.resolve() in protected_paths:
                kept += 1
                continue
            if kept < keep:
                kept += 1
                continue
            assert_safe_candidate(old, artifacts_root)
            candidates.append(Candidate(old, f"retention:{prefix}:keep={keep}", path_size(old)))
    if artifacts_root.exists():
        for bak in sorted(artifacts_root.rglob(".bak.*"), key=lambda p: str(p)):
            if bak.resolve() in protected_paths:
                continue
            assert_safe_candidate(bak, artifacts_root)
            candidates.append(Candidate(bak, "remove:.bak.*", path_size(bak)))
    # Deduplicate while preserving deterministic order.
    seen: set[Path] = set()
    out: list[Candidate] = []
    for c in candidates:
        rp = c.path.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(c)
    return out


def delete_candidate(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        # Path may have disappeared between dry-run and apply.
        return


def summarize(artifacts_root: Path) -> dict[str, Any]:
    def names(prefix: str) -> list[str]:
        if not artifacts_root.exists():
            return []
        return [p.name for p in sorted(artifacts_root.iterdir(), key=newest_key, reverse=True) if p.name.startswith(prefix)]
    bak = [str(p.relative_to(artifacts_root)) for p in artifacts_root.rglob(".bak.*")] if artifacts_root.exists() else []
    return {
        "daily_clean_count": len(names("daily-clean-stage-")),
        "daily_clean": names("daily-clean-stage-"),
        "daily_tech_count": len(names("daily-tech-stage-")),
        "daily_tech": names("daily-tech-stage-"),
        "app_pre_promote_count": len(names("app.pre-promote-")),
        "app_pre_promote": names("app.pre-promote-"),
        "bak_count": len(bak),
        "app_pre_rollback_count": len(names("app.pre-rollback-")),
        "app_pre_rollback": names("app.pre-rollback-"),
        "bak": bak,
    }


def run(
    artifacts_root: Path,
    *,
    apply: bool,
    keep_daily: int = 1,
    keep_pre_promote: int = 1,
    protected_paths: set[Path] | None = None,
) -> dict[str, Any]:
    artifacts_root = artifacts_root.resolve()
    protected_paths = {p.resolve() for p in (protected_paths or set())}
    candidates = retention_candidates(
        artifacts_root,
        keep_daily=keep_daily,
        keep_pre_promote=keep_pre_promote,
        protected_paths=protected_paths,
    )
    before = summarize(artifacts_root)
    deleted: list[dict[str, Any]] = []
    for c in candidates:
        assert_safe_candidate(c.path, artifacts_root)
        item = {"path": str(c.path), "reason": c.reason, "size_bytes": c.size_bytes}
        if apply:
            delete_candidate(c.path)
            item["deleted"] = True
        else:
            item["deleted"] = False
        deleted.append(item)
    after = summarize(artifacts_root)
    return {
        "ok": True,
        "mode": "apply" if apply else "dry-run",
        "generated_at": utcnow(),
        "artifacts_root": str(artifacts_root),
        "protected_names": sorted(PROTECTED_NAMES),
        "keep_daily": keep_daily,
        "keep_pre_promote": keep_pre_promote,
        "protected_paths": [str(p) for p in sorted(protected_paths, key=str)],
        "before": before,
        "delete_count": len(deleted),
        "delete_candidates": deleted,
        "after": after,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase E artifact retention")
    ap.add_argument("--artifacts", default=str(DEFAULT_ARTIFACTS))
    ap.add_argument("--keep-daily", type=int, default=1)
    ap.add_argument("--keep-pre-promote", type=int, default=1)
    ap.add_argument("--protect", action="append", default=[], help="extra artifact path to protect from deletion; may be repeated")
    ap.add_argument("--apply", action="store_true", help="actually delete candidates; default is dry-run")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    report = run(
        Path(args.artifacts),
        apply=args.apply,
        keep_daily=args.keep_daily,
        keep_pre_promote=args.keep_pre_promote,
        protected_paths={Path(p) for p in args.protect},
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
