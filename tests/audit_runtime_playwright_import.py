#!/usr/bin/env python3
"""Runtime gate for the exact Python used by immo_daily_public_refresh.sh.

It catches the failure class where a Python 3.12 project venv imports
Playwright/greenlet from a Python 3.13 user-site package path.
"""
from __future__ import annotations

import importlib
import json
import os
import site
import sys
from pathlib import Path


def module_file(name: str) -> str:
    module = importlib.import_module(name)
    return str(getattr(module, "__file__", ""))


def main() -> int:
    executable = Path(sys.executable).resolve()
    project = Path(__file__).resolve().parents[1]
    expected_venv = (project / ".venv").resolve()
    path_entries = [str(p) for p in sys.path]

    payload: dict[str, object] = {
        "executable": str(executable),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "version": sys.version.split()[0],
        "enable_user_site": site.ENABLE_USER_SITE,
        "user_site": site.getusersitepackages(),
        "pythonpath": os.environ.get("PYTHONPATH"),
        "path": path_entries,
    }

    errors: list[str] = []
    # CPython may resolve sys.executable to the uv-managed base binary even when
    # invoked through .venv/bin/python. The invariant that matters is the active
    # venv prefix and import location, not the symlink-resolved executable path.
    if Path(sys.prefix).resolve() != expected_venv:
        errors.append(f"unexpected venv prefix: {sys.prefix} != {expected_venv}")
    if site.ENABLE_USER_SITE:
        errors.append("user site is enabled inside project venv")
    foreign_user_paths = [p for p in path_entries if "/.local/lib/python3.13/site-packages" in p]
    if foreign_user_paths:
        errors.append(f"foreign Python 3.13 user-site on sys.path: {foreign_user_paths}")

    for name in ("greenlet", "greenlet._greenlet", "playwright", "playwright.sync_api"):
        try:
            payload[name] = module_file(name)
        except Exception as exc:  # pragma: no cover - failure path prints diagnostics for shell gate
            payload[name] = f"{type(exc).__name__}: {exc}"
            errors.append(f"cannot import {name}: {type(exc).__name__}: {exc}")

    for name in ("greenlet", "playwright"):
        value = str(payload.get(name, ""))
        if value and str(expected_venv) not in value:
            errors.append(f"{name} imported outside project venv: {value}")

    payload["ok"] = not errors
    payload["errors"] = errors
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
