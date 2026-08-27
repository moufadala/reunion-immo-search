#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from build_daily_edition import main


if __name__ == "__main__":
    if "--check-canary" not in sys.argv and "--run-dir" not in sys.argv:
        sys.argv.insert(1, "--check-canary")
    raise SystemExit(main())
