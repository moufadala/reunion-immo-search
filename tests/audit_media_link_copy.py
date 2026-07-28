#!/usr/bin/env python3
"""Audit media-only hardlink copy helpers and promotion/rollback behavior."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "media_link_copy.py"
PROMOTE = ROOT / "scripts" / "promote_app_candidate.py"
ROLLBACK = ROOT / "scripts" / "rollback_public_app.py"

errors: list[str] = []


def sha_tree(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(x for x in path.rglob("*") if x.is_file()):
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        out[str(p.relative_to(path))] = h
    return out


def assert_hardlinked(a: Path, b: Path, label: str) -> None:
    sa = a.stat()
    sb = b.stat()
    if (sa.st_dev, sa.st_ino) != (sb.st_dev, sb.st_ino):
        errors.append(f"{label}: expected same inode, got {(sa.st_dev, sa.st_ino)} vs {(sb.st_dev, sb.st_ino)}")
    if sa.st_nlink < 2:
        errors.append(f"{label}: expected nlink >= 2, got {sa.st_nlink}")


def assert_not_hardlinked(a: Path, b: Path, label: str) -> None:
    sa = a.stat()
    sb = b.stat()
    if (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino):
        errors.append(f"{label}: expected independent inode, got shared {(sa.st_dev, sa.st_ino)}")


def write_app(path: Path, marker: str) -> None:
    (path / "thumbs").mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text(f"<html>{marker}</html>", encoding="utf-8")
    (path / "listings.json").write_text(json.dumps({"listings": [{"id": marker, "source": "seloger"}]}), encoding="utf-8")
    (path / "thumbs" / "same.jpg").write_bytes(b"JPEGDATA")
    (path / "thumbs" / "same.webp").write_bytes(b"WEBPDATA")
    (path / "notes.txt").write_text(marker, encoding="utf-8")
    (path / "morning-reports" / "2026-07-21" / "files").mkdir(parents=True, exist_ok=True)
    (path / "morning-reports" / "2026-07-21" / "files" / "report.html").write_text("report", encoding="utf-8")
    os.symlink("2026-07-21/files", path / "morning-reports" / "files")


with tempfile.TemporaryDirectory(prefix="immo-media-link-audit-") as td:
    tmp = Path(td)
    src = tmp / "src"
    dst = tmp / "dst"
    write_app(src, "src")

    if not HELPER.exists():
        errors.append(f"missing helper {HELPER}")
    else:
        sys.path.insert(0, str(ROOT / "scripts"))
        from media_link_copy import copytree_media_aware  # type: ignore

        stats = copytree_media_aware(src, dst, media_mode="hardlink")
        if stats.media_linked != 2 or stats.files_copied < 3:
            errors.append(f"unexpected copy stats: {stats}")
        seeded = tmp / "seeded"
        (seeded / "thumbs").mkdir(parents=True)
        (seeded / "thumbs" / "same.jpg").write_bytes(b"KEEP_EXISTING")
        seed_stats = copytree_media_aware(src / "thumbs", seeded / "thumbs", media_mode="hardlink", dirs_exist_ok=True, existing="skip")
        if (seeded / "thumbs" / "same.jpg").read_bytes() != b"KEEP_EXISTING" or seed_stats.media_linked != 1:
            errors.append(f"cache seed skip/no-clobber behavior failed: {seed_stats}")
        assert_hardlinked(src / "thumbs" / "same.webp", seeded / "thumbs" / "same.webp", "seed webp hardlink")
        assert_hardlinked(src / "thumbs" / "same.jpg", dst / "thumbs" / "same.jpg", "jpg copy")
        assert_hardlinked(src / "thumbs" / "same.webp", dst / "thumbs" / "same.webp", "webp copy")
        assert_not_hardlinked(src / "index.html", dst / "index.html", "html copy")
        assert_not_hardlinked(src / "listings.json", dst / "listings.json", "json copy")
        if not (dst / "morning-reports" / "files").is_symlink() or os.readlink(dst / "morning-reports" / "files") != "2026-07-21/files":
            errors.append("symlinked morning-reports/files was not preserved as a symlink")
        # Atomic image writer proof: tmp.replace(out) should break the link for dst only.
        out = dst / "thumbs" / "same.jpg"
        tmp_img = out.with_suffix(out.suffix + ".tmp")
        tmp_img.write_bytes(b"NEWJPEG")
        tmp_img.replace(out)
        assert_not_hardlinked(src / "thumbs" / "same.jpg", out, "atomic replace breaks image hardlink")
        if (src / "thumbs" / "same.jpg").read_bytes() != b"JPEGDATA":
            errors.append("atomic replace changed source image content")

    # Real promote then real rollback drill in temp tree.
    target = tmp / "target"
    candidate = tmp / "candidate"
    write_app(target, "old")
    write_app(candidate, "new")
    candidate_sha = sha_tree(candidate)
    old_sha = sha_tree(target)
    promote_json = tmp / "promote.json"
    promote = subprocess.run(
        [sys.executable, str(PROMOTE), "--candidate", str(candidate), "--target", str(target), "--media-copy-mode", "hardlink", "--json-out", str(promote_json)],
        cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    if promote.returncode != 0:
        errors.append(f"promote failed rc={promote.returncode}\nSTDOUT={promote.stdout}\nSTDERR={promote.stderr}")
    else:
        payload = json.loads(promote_json.read_text(encoding="utf-8"))
        backup = Path(payload["backup"])
        if sha_tree(backup) != old_sha:
            errors.append("pre-promote backup SHA tree differs from original target")
        if sha_tree(target) != candidate_sha:
            errors.append("promoted target SHA tree differs from candidate")
        assert_hardlinked(candidate / "thumbs" / "same.jpg", target / "thumbs" / "same.jpg", "post-promotion jpg candidate->target")
        rollback_json = tmp / "rollback.json"
        rollback = subprocess.run(
            [sys.executable, str(ROLLBACK), "--apply", "--backup", str(backup), "--target", str(target), "--media-copy-mode", "hardlink", "--json-out", str(rollback_json)],
            cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
        if rollback.returncode != 0:
            errors.append(f"rollback failed rc={rollback.returncode}\nSTDOUT={rollback.stdout}\nSTDERR={rollback.stderr}")
        elif sha_tree(target) != old_sha:
            errors.append("real rollback restored tree SHA differs from original target")
        else:
            assert_hardlinked(backup / "thumbs" / "same.jpg", target / "thumbs" / "same.jpg", "rollback jpg backup->target")

print("MEDIA_LINK_COPY_AUDIT", "PASS" if not errors else "FAIL")
if errors:
    for e in errors:
        print(" -", e)
    sys.exit(1)
print(json.dumps({"ok": True, "checks": ["media-only hardlinks", "html/json copied", "atomic image replace breaks link", "real promote", "real rollback SHA restore", "post-promotion nlink"]}, ensure_ascii=False, indent=2))
