"""Canonical local photo galleries and verify their public integrity.

The module never writes, moves, or deletes photo files.  It only returns a
first-occurrence-wins view of a gallery.  Exact media URLs are compared first;
when an ``app_root`` is supplied, existing local files are then compared by
SHA-256 so differently named copies of the same bytes collapse as well.
"""
from __future__ import annotations

import hashlib
import posixpath
from pathlib import Path
from typing import Any, Iterable, MutableMapping
from urllib.parse import unquote, urlsplit


HashCache = MutableMapping[Path, str]


def _usable_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _media_key(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        path = posixpath.normpath(parsed.path or "/")
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}?{parsed.query}"
    path = posixpath.normpath(unquote(parsed.path).replace("\\", "/"))
    return path.lstrip("/") + (f"?{parsed.query}" if parsed.query else "")


def _local_path(value: str, app_root: Path) -> tuple[Path | None, str | None]:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None, "external"
    raw_path = unquote(parsed.path).replace("\\", "/")
    relative = Path(raw_path.lstrip("/"))
    root = app_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, "unsafe"
    if not candidate.is_file():
        return None, "missing"
    return candidate, None


def _sha256(path: Path, cache: HashCache) -> str:
    cached = cache.get(path)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    value = digest.hexdigest()
    cache[path] = value
    return value


def _image_content_status(path: Path) -> str | None:
    """Return invalid/unreadable or None for a recognized image file."""
    try:
        if path.stat().st_size <= 0:
            return "invalid"
        with path.open("rb") as handle:
            header = handle.read(32)
    except OSError:
        return "unreadable"

    recognized = (
        header.startswith(b"\xff\xd8\xff")
        or header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith((b"GIF87a", b"GIF89a"))
        or header.startswith(b"BM")
        or header.startswith((b"II*\x00", b"MM\x00*"))
        or (
            len(header) >= 12
            and header.startswith(b"RIFF")
            and header[8:12] == b"WEBP"
        )
        or (
            len(header) >= 12
            and header[4:8] == b"ftyp"
            and header[8:12] in {b"avif", b"avis", b"heic", b"heix", b"mif1"}
        )
    )
    return None if recognized else "invalid"

def canonicalize_gallery(
    values: Iterable[object],
    *,
    app_root: Path | str | None = None,
    hash_cache: HashCache | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Return a stable gallery with repeated URLs/content removed.

    Unreadable, missing, unsafe, and external paths are retained: absence of a
    hash is not evidence that two photos are identical.  Their counts remain in
    the report so another public-file gate can handle them explicitly.
    """
    root = Path(app_root) if app_root is not None else None
    cache: HashCache = hash_cache if hash_cache is not None else {}
    kept: list[str] = []
    seen_urls: dict[str, str] = {}
    seen_hashes: dict[str, str] = {}
    repeated_urls: list[dict[str, str]] = []
    repeated_content: dict[str, list[str]] = {}
    missing_files = unsafe_paths = external_urls = unreadable_files = 0
    invalid_image_files = 0
    input_count = 0

    for raw in values:
        value = _usable_url(raw)
        if value is None:
            continue
        input_count += 1
        key = _media_key(value)
        if key in seen_urls:
            repeated_urls.append({"kept": seen_urls[key], "removed": value})
            continue

        digest: str | None = None
        if root is not None:
            path, status = _local_path(value, root)
            if status == "external":
                external_urls += 1
            elif status == "unsafe":
                unsafe_paths += 1
            elif status == "missing":
                missing_files += 1
            elif path is not None:
                image_status = _image_content_status(path)
                if image_status == "invalid":
                    invalid_image_files += 1
                elif image_status == "unreadable":
                    unreadable_files += 1
                else:
                    try:
                        digest = _sha256(path, cache)
                    except OSError:
                        unreadable_files += 1

        if digest is not None and digest in seen_hashes:
            first = seen_hashes[digest]
            repeated_content.setdefault(digest, [first]).append(value)
            seen_urls[key] = value
            continue

        kept.append(value)
        seen_urls[key] = value
        if digest is not None:
            seen_hashes[digest] = value

    repeated_content_rows = [
        {"sha256": digest, "paths": paths}
        for digest, paths in repeated_content.items()
    ]
    report = {
        "input": input_count,
        "kept": len(kept),
        "duplicate_urls": len(repeated_urls),
        "duplicate_content": sum(len(row["paths"]) - 1 for row in repeated_content_rows),
        "repeated_urls": repeated_urls,
        "repeated_content": repeated_content_rows,
        "missing_files": missing_files,
        "unsafe_paths": unsafe_paths,
        "external_urls": external_urls,
        "invalid_image_files": invalid_image_files,
        "unreadable_files": unreadable_files,
    }
    return kept, report


def canonicalize_listing_gallery(
    item: dict[str, Any],
    *,
    app_root: Path | str | None = None,
    hash_cache: HashCache | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a listing copy whose cover and gallery share one canonical order."""
    output = dict(item)
    raw_images = item.get("images") if isinstance(item.get("images"), list) else []
    primary = _usable_url(item.get("image"))
    candidates: list[object] = []
    if primary is not None:
        candidates.append(primary)
        primary_key = _media_key(primary)
        candidates.extend(
            image for image in raw_images
            if not isinstance(image, str) or _media_key(image.strip()) != primary_key
        )
    else:
        candidates.extend(raw_images)
    gallery, report = canonicalize_gallery(
        candidates,
        app_root=app_root,
        hash_cache=hash_cache,
    )
    output["images"] = gallery
    output["image"] = gallery[0] if gallery else None
    return output, report


def audit_public_galleries(
    listings: Iterable[dict[str, Any]],
    *,
    app_root: Path | str,
    hash_cache: HashCache | None = None,
) -> dict[str, Any]:
    """Audit the actual ``images`` arrays; repeated URL/content is blocking."""
    cache: HashCache = hash_cache if hash_cache is not None else {}
    root = Path(app_root)
    violations: list[dict[str, Any]] = []
    shared_by_digest: dict[str, dict[str, set[str]]] = {}
    shared_placeholder_min_listing_ids = 5
    listing_count = gallery_count = 0
    url_gallery_count = content_gallery_count = 0
    repeated_url_count = repeated_content_count = 0
    invalid_image_files = 0
    missing_files = unsafe_paths = external_urls = unreadable_files = 0

    for item in listings:
        if not isinstance(item, dict):
            continue
        listing_count += 1
        images = item.get("images") if isinstance(item.get("images"), list) else []
        if not images and _usable_url(item.get("image")) is not None:
            images = [item["image"]]
        if images:
            gallery_count += 1
        _, report = canonicalize_gallery(images, app_root=app_root, hash_cache=cache)
        missing_files += report["missing_files"]
        unsafe_paths += report["unsafe_paths"]
        external_urls += report["external_urls"]
        invalid_image_files += report["invalid_image_files"]
        unreadable_files += report["unreadable_files"]
        for field, violation_type in (
            ("missing_files", "missing_local_file"),
            ("unsafe_paths", "unsafe_local_path"),
            ("invalid_image_files", "invalid_local_image"),
            ("unreadable_files", "unreadable_local_file"),
        ):
            if report[field]:
                violations.append({
                    "type": violation_type,
                    "id": item.get("id"),
                    "count": report[field],
                })
        if report["kept"] and report["external_urls"] == report["kept"]:
            violations.append({
                "type": "external_only_gallery",
                "id": item.get("id"),
                "count": report["external_urls"],
            })
        repeated_url_count += report["duplicate_urls"]
        repeated_content_count += report["duplicate_content"]
        if report["duplicate_urls"]:
            url_gallery_count += 1
        if report["duplicate_content"]:
            content_gallery_count += 1
        if report["duplicate_urls"] or report["duplicate_content"]:
            violations.append({
                "type": "intra_gallery_duplicate",
                "id": item.get("id"),
                "repeated_urls": report["repeated_urls"],
                "repeated_content": report["repeated_content"],
            })

        source = str(item.get("source") or "").strip().lower()
        listing_id = str(item.get("id") or "").strip()
        business_id = listing_id.split(":", 1)[-1]
        listing_hashes: set[str] = set()
        if listing_id:
            for raw in images:
                value = _usable_url(raw)
                if value is None:
                    continue
                path, status = _local_path(value, root)
                if path is None or status is not None:
                    continue
                if _image_content_status(path) is not None:
                    continue
                try:
                    digest = _sha256(path, cache)
                except OSError:
                    continue
                if digest in listing_hashes:
                    continue
                listing_hashes.add(digest)
                bucket = shared_by_digest.setdefault(
                    digest,
                    {
                        "business_ids": set(),
                        "listing_ids": set(),
                        "paths": set(),
                        "sources": set(),
                    },
                )
                bucket["business_ids"].add(business_id)
                bucket["listing_ids"].add(listing_id)
                bucket["paths"].add(value)
                if source:
                    bucket["sources"].add(source)

    suspected_shared_placeholders = []
    for digest, bucket in sorted(shared_by_digest.items()):
        listing_ids = sorted(bucket["listing_ids"])
        business_ids = sorted(bucket["business_ids"])
        if len(listing_ids) < shared_placeholder_min_listing_ids:
            continue
        sources = sorted(bucket["sources"])
        suspected_shared_placeholders.append({
            "source": sources[0] if len(sources) == 1 else None,
            "sources": sources,
            "sha256": digest,
            "listing_count": len(listing_ids),
            "listing_ids": listing_ids,
            "business_ids": business_ids,
            "paths": sorted(bucket["paths"]),
            "threshold": shared_placeholder_min_listing_ids,
        })
    violations.extend(
        {"type": "shared_placeholder", **suspicion}
        for suspicion in suspected_shared_placeholders
    )

    return {
        "ok": not violations,
        "listings": listing_count,
        "galleries": gallery_count,
        "galleries_with_url_duplicates": url_gallery_count,
        "galleries_with_content_duplicates": content_gallery_count,
        "repeated_urls": repeated_url_count,
        "repeated_content": repeated_content_count,
        "missing_files": missing_files,
        "unsafe_paths": unsafe_paths,
        "external_urls": external_urls,
        "invalid_image_files": invalid_image_files,
        "unreadable_files": unreadable_files,
        "shared_placeholder_suspicions": len(suspected_shared_placeholders),
        "suspected_shared_placeholders": suspected_shared_placeholders,
        "violations": violations,
    }
