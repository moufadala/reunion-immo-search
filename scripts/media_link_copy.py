#!/usr/bin/env python3
"""Media-only hardlink-aware tree copy utilities for generated immo artifacts.

The safe contract is deliberately narrow: image/media files may be hardlinked;
HTML/JSON/text/runtime files are copied as independent inodes because several
pipeline steps rewrite them in place.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

MEDIA_SUFFIXES = {".jpg", ".jpeg", ".webp", ".png", ".gif", ".avif"}
VALID_MEDIA_MODES = {"copy", "hardlink"}


@dataclass
class MediaCopyStats:
    src: str
    dst: str
    media_mode: str
    files_copied: int = 0
    media_linked: int = 0
    media_copied: int = 0
    dirs_created: int = 0
    bytes_logical: int = 0
    bytes_linked: int = 0
    link_fallbacks: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def is_media_file(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_SUFFIXES


def _copy_file(src: Path, dst: Path, *, media_mode: str, stats: MediaCopyStats) -> None:
    size = src.stat().st_size
    stats.bytes_logical += size
    if media_mode == "hardlink" and is_media_file(src):
        try:
            os.link(src, dst)
            shutil.copystat(src, dst, follow_symlinks=True)
            stats.media_linked += 1
            stats.bytes_linked += size
            return
        except OSError:
            # Cross-device or permission corner case: preserve correctness by copying.
            stats.link_fallbacks += 1
    shutil.copy2(src, dst)
    if is_media_file(src):
        stats.media_copied += 1
    else:
        stats.files_copied += 1


def copytree_media_aware(
    src: Path,
    dst: Path,
    *,
    media_mode: str = "copy",
    dirs_exist_ok: bool = False,
    existing: str = "error",
) -> MediaCopyStats:
    """Copy src to dst, hardlinking only media when requested.

    By default this mirrors shutil.copytree's safety: destination must not
    already exist. For cache seeding, callers may use dirs_exist_ok=True plus
    existing='skip' to reproduce `cp -an` merge/no-clobber behavior.
    """
    src = Path(src)
    dst = Path(dst)
    if media_mode not in VALID_MEDIA_MODES:
        raise ValueError(f"invalid media_mode={media_mode!r}; expected one of {sorted(VALID_MEDIA_MODES)}")
    if existing not in {"error", "skip", "overwrite"}:
        raise ValueError("existing must be one of: error, skip, overwrite")
    if not src.is_dir():
        raise FileNotFoundError(f"source directory missing: {src}")
    if dst.exists() and not dirs_exist_ok:
        raise FileExistsError(f"destination already exists: {dst}")

    stats = MediaCopyStats(src=str(src), dst=str(dst), media_mode=media_mode)
    if not dst.exists():
        dst.mkdir(parents=True)
        stats.dirs_created += 1
    for root, dirs, files in os.walk(src):
        root_path = Path(root)
        rel_root = root_path.relative_to(src)
        for d in list(dirs):
            source_dir = root_path / d
            target_dir = dst / rel_root / d
            if source_dir.is_symlink():
                # Match shutil.copytree(..., symlinks=True): preserve the link
                # itself instead of creating an empty real directory.
                dirs.remove(d)
                if target_dir.exists():
                    if existing == "skip":
                        continue
                    if existing == "overwrite":
                        target_dir.unlink()
                    else:
                        raise FileExistsError(f"destination path already exists: {target_dir}")
                os.symlink(os.readlink(source_dir), target_dir)
                stats.files_copied += 1
                continue
            if target_dir.exists():
                continue
            target_dir.mkdir()
            shutil.copystat(source_dir, target_dir, follow_symlinks=False)
            stats.dirs_created += 1
        for name in files:
            source_file = root_path / name
            target_file = dst / rel_root / name
            if target_file.exists():
                if existing == "skip":
                    continue
                if existing == "overwrite":
                    target_file.unlink()
                else:
                    raise FileExistsError(f"destination file already exists: {target_file}")
            if source_file.is_symlink():
                os.symlink(os.readlink(source_file), target_file)
                stats.files_copied += 1
                continue
            _copy_file(source_file, target_file, media_mode=media_mode, stats=stats)
    shutil.copystat(src, dst, follow_symlinks=False)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Copy a tree while hardlinking media files only.")
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--media-mode", choices=sorted(VALID_MEDIA_MODES), default=os.environ.get("IMMO_MEDIA_COPY_MODE", "copy"))
    ap.add_argument("--dirs-exist-ok", action="store_true")
    ap.add_argument("--existing", choices=["error", "skip", "overwrite"], default="error")
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()
    stats = copytree_media_aware(args.src, args.dst, media_mode=args.media_mode, dirs_exist_ok=args.dirs_exist_ok, existing=args.existing)
    payload = stats.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
