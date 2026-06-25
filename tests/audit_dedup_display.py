#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def fail(msg: str) -> None:
    print(f"DEDUP_DISPLAY_AUDIT_FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/app")
    data = json.loads((app / "dedup_groups.json").read_text(encoding="utf-8"))
    html = (app / "dedup.html").read_text(encoding="utf-8")
    groups = data.get("groups") or []
    if data.get("groups_count", 0) < 1:
        fail("no duplicate groups")
    for idx, g in enumerate(groups[:20]):
        if g.get("decision") not in {"auto_duplicate", "needs_review"}:
            fail(f"bad decision group {idx}")
        if not g.get("explanations"):
            fail(f"missing explanations group {idx}")
        if "aucune suppression" not in (g.get("policy") or ""):
            fail(f"policy is not explicitly non destructive group {idx}")
        if len(g.get("links") or []) < 2:
            fail(f"missing member links group {idx}")
    for token in ["Déduplication douce", "À revoir humainement", "décision", "Pourquoi"]:
        if token not in html:
            fail(f"missing HTML token: {token}")
    print("DEDUP_DISPLAY_AUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
