"""Resolve and launch the exact installed Chromium used by browser QA."""
from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_ROOTS = (
    Path("/opt/data/.cache/ms-playwright"),
    Path("/opt/data/home/.cache/ms-playwright"),
)


def _search_roots() -> tuple[Path, ...]:
    configured = os.environ.get("IMMO_BROWSER_SEARCH_ROOTS")
    if configured:
        return tuple(Path(value) for value in configured.split(os.pathsep) if value)
    playwright_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    roots = ((Path(playwright_root),) if playwright_root else ()) + DEFAULT_ROOTS
    return tuple(dict.fromkeys(roots))


def resolve_browser_executable() -> Path:
    explicit = os.environ.get("IMMO_BROWSER_EXECUTABLE")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise RuntimeError(f"IMMO_BROWSER_EXECUTABLE must be absolute: {path}")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(f"IMMO_BROWSER_EXECUTABLE is invalid: {path}: {exc}") from exc
        if resolved.is_file():
            return resolved
        raise RuntimeError(f"IMMO_BROWSER_EXECUTABLE is not a file: {path}")

    matches: dict[Path, int] = {}
    for configured_root in _search_roots():
        try:
            root = configured_root.expanduser().resolve(strict=True)
        except OSError:
            continue
        for path in root.glob("chromium-*/chrome-linux64/chrome"):
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if not resolved.is_file():
                continue
            match = re.fullmatch(r"chromium-(\d+)", path.parents[1].name)
            if match:
                matches[resolved] = int(match.group(1))
    if not matches:
        roots = ", ".join(str(root) for root in _search_roots())
        raise RuntimeError(f"No installed Chromium executable found under: {roots}")
    return max(matches, key=lambda path: (matches[path], str(path)))

def launch_chromium(playwright):
    """Launch the discovered installed Chromium; never download at runtime."""
    executable = resolve_browser_executable()
    return playwright.chromium.launch(headless=True, executable_path=str(executable))
