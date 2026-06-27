#!/usr/bin/env python3
"""
Lot C — Semantic & Source Quality Matrix
Consolidated gate: verifies that listings.json contains sufficient evidence
for all displayable fields, description_status, locations, photos, source_url,
and produces a JSON + Markdown report with distributions.

Usage:
    # Read-only default: reports go to a run-scoped /tmp/immo-qa-* directory.
    python3 tests/audit_semantic_source_quality_matrix.py --app artifacts/app

    # Durable reports are opt-in; pass explicit output paths when needed.
    python3 tests/audit_semantic_source_quality_matrix.py \\
        --app artifacts/app \\
        --json-out /tmp/immo-qa-example/semantic_source_quality.json \\
        --md-out /tmp/immo-qa-example/semantic_source_quality.md

Exit code: 0 = pass, 1 = P0 failure (blocking).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

# ── constants ──────────────────────────────────────────────────────────────
BOILERPLATE_MARKERS: list[str] = [
    "L'annonce a bien été ajoutée à vos favoris",
    "Annonce publiée le",
    "Proposée par",
    "Cette annonce vous est proposée par",
    "Extrait de notre barème",
    "Réf. annonce",
]

TECH_FRAGMENT_PATTERNS: list[str] = [
    r"serp_view",
    r"query\s*brute",
    r"raw_query",
    r"internal\s*path",
    r"debug\s*=",
    r"__pycache__",
    r"/tests/",
    r"Traceback",
    r"Error:",
    r"NoneType",
    r"ja[vw]ascri[pt]",
    r"function\s*\(",
]

# ── helpers ────────────────────────────────────────────────────────────────


def default_qa_out(name: str) -> str:
    """Return a temp report path so the audit is read-only by default."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return str(Path(tempfile.gettempdir()) / f"immo-qa-{stamp}" / name)


def load_listings(app: Path) -> tuple[list[dict], str]:
    """Load and validate listings.json. Returns (items, raw_json) or raises."""
    p = app / "listings.json"
    if not p.exists():
        raise SystemExit(f"P0 — listings.json not found: {p}")
    raw = p.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"P0 — invalid JSON in {p}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"P0 — listings.json root is not a dict (got {type(data).__name__})")
    items = data.get("listings")
    if not isinstance(items, list):
        raise SystemExit(f"P0 — listings key is missing or not a list")
    return items, raw


def safe_str(val: object) -> str:
    return str(val).strip() if val else ""


def fmt_pct(num: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{100.0 * num / total:.1f}%"


# ── check functions ────────────────────────────────────────────────────────


def check_listings_count(items: list[dict]) -> dict:
    """Gate: listings count > 0 (P0)."""
    n = len(items)
    return {
        "check": "listings_count",
        "status": "PASS" if n > 0 else "P0_FAIL",
        "value": n,
        "threshold": "> 0",
        "note": "" if n > 0 else "P0 — zero listings!",
    }


def check_json_valid(raw_json: str) -> dict:
    """Already validated in load_listings — just a formality."""
    return {
        "check": "json_valid",
        "status": "PASS",
        "value": len(raw_json),
        "threshold": "parseable",
        "note": f"{len(raw_json)} bytes",
    }


def check_source_distribution(items: list[dict]) -> dict:
    """Source distribution report (non-blocking)."""
    src = Counter(x.get("source", "?") for x in items)
    return {
        "check": "source_distribution",
        "status": "REPORT",
        "value": dict(src.most_common()),
        "threshold": "n/a",
        "note": f"{len(src)} distinct sources",
    }


def check_field_coverage(items: list[dict]) -> dict:
    """Coverage for price, surface, city, source_url (url)."""
    total = len(items)
    price = sum(1 for x in items if x.get("price") is not None)
    surface = sum(1 for x in items if x.get("surface") is not None and x.get("surface") != "")
    city = sum(1 for x in items if safe_str(x.get("city")))
    url = sum(1 for x in items if safe_str(x.get("url")))
    bedrooms = sum(1 for x in items if x.get("bedrooms") is not None)
    rooms = sum(1 for x in items if x.get("rooms") is not None)
    type_field = sum(1 for x in items if safe_str(x.get("type")))
    district = sum(1 for x in items if safe_str(x.get("district")))

    p0_failures = []

    # P0: source_url coverage catastrophic (< 50%)
    url_pct = 100.0 * url / total if total else 0
    if url_pct < 50.0:
        p0_failures.append(f"source_url coverage catastrophic: {url_pct:.1f}%")

    report = {
        "check": "field_coverage",
        "status": "PASS" if not p0_failures else "P0_FAIL",
        "value": {
            "total": total,
            "price": {"count": price, "pct": fmt_pct(price, total)},
            "surface": {"count": surface, "pct": fmt_pct(surface, total)},
            "city": {"count": city, "pct": fmt_pct(city, total)},
            "source_url": {"count": url, "pct": fmt_pct(url, total)},
            "bedrooms": {"count": bedrooms, "pct": fmt_pct(bedrooms, total)},
            "rooms": {"count": rooms, "pct": fmt_pct(rooms, total)},
            "type": {"count": type_field, "pct": fmt_pct(type_field, total)},
            "district": {"count": district, "pct": fmt_pct(district, total)},
        },
        "threshold": "source_url >= 50% (P0); others report only",
        "note": "; ".join(p0_failures) if p0_failures else "all fields within normal range",
    }
    return report


def check_descriptions(items: list[dict], max_empty: int = 0) -> dict:
    """Description quality: empty, fallback, short, boilerplate, technical fragments."""
    total = len(items)
    statuses = Counter(safe_str(x.get("description_status")) for x in items)

    empty = [x for x in items if not safe_str(x.get("description"))]
    desc_lens = [len(safe_str(x.get("description"))) for x in items]
    short = [x for x in items if 0 < len(safe_str(x.get("description"))) < 30]

    # Fallback = synthetic/generated descriptions
    fallback = [
        x
        for x in items
        if "Synth" in safe_str(x.get("description_status"))
        or "synth" in safe_str(x.get("description_status"))
    ]

    source = [x for x in items if safe_str(x.get("description_status")) == "Description source"]
    no_analysis = [x for x in items if not x.get("description_analysis")]

    # Boilerplate
    boilerplate: list[dict] = []
    for x in items:
        text = safe_str(x.get("description"))
        hits = [m for m in BOILERPLATE_MARKERS if m in text]
        if hits:
            boilerplate.append({"id": x.get("id"), "source": x.get("source"), "markers": hits})

    # Technical fragments in descriptions + titles
    tech_fragments: list[dict] = []
    for x in items:
        combined = safe_str(x.get("description")) + " " + safe_str(x.get("title"))
        finds = [p for p in TECH_FRAGMENT_PATTERNS if re.search(p, combined, re.I)]
        if finds:
            tech_fragments.append(
                {
                    "id": x.get("id"),
                    "source": x.get("source"),
                    "patterns": finds,
                    "title": safe_str(x.get("title"))[:80],
                }
            )

    # HTML/XML fragments
    html_fragments: list[dict] = []
    for x in items:
        d = safe_str(x.get("description"))
        if "<" in d and ">" in d and not d.startswith("<"):
            html_fragments.append({"id": x.get("id"), "preview": d[:100]})

    p0_failures = []
    if len(empty) > max_empty:
        p0_failures.append(f"P0 — empty descriptions: {len(empty)} > {max_empty}")

    report: dict = {
        "check": "descriptions",
        "status": "PASS" if not p0_failures else "P0_FAIL",
        "value": {
            "total": total,
            "description_status": dict(statuses),
            "source_descriptions": len(source),
            "fallback_descriptions": len(fallback),
            "empty_descriptions": len(empty),
            "short_descriptions": len(short),
            "no_description_analysis": len(no_analysis),
            "boilerplate_count": len(boilerplate),
            "tech_fragment_count": len(tech_fragments),
            "html_fragment_count": len(html_fragments),
            "lengths": {
                "min": min(desc_lens) if desc_lens else 0,
                "median": median(desc_lens) if desc_lens else 0,
                "max": max(desc_lens) if desc_lens else 0,
            },
        },
        "threshold": f"empty <= {max_empty} (P0); fallback, boilerplate, tech fragments reported",
        "note": "; ".join(p0_failures) if p0_failures else "descriptions within normal range",
    }
    return report


def check_photos(items: list[dict]) -> dict:
    """Photo coverage: main image, gallery, gallery_status."""
    total = len(items)
    main_img = sum(1 for x in items if x.get("image_url"))
    local_img = sum(1 for x in items if x.get("local_image_url"))
    local_urls = sum(1 for x in items if x.get("local_image_urls") and len(x["local_image_urls"]) > 0)
    multi_photo = sum(1 for x in items if x.get("local_image_urls") and len(x["local_image_urls"]) > 1)
    gs_dist = Counter(safe_str(x.get("gallery_status")) for x in items)
    pc_dist = Counter(str(x.get("photo_cached", "?")) for x in items)

    return {
        "check": "photos",
        "status": "REPORT",
        "value": {
            "total": total,
            "main_image_url": {"count": main_img, "pct": fmt_pct(main_img, total)},
            "local_image_url": {"count": local_img, "pct": fmt_pct(local_img, total)},
            "local_image_urls_nonempty": {"count": local_urls, "pct": fmt_pct(local_urls, total)},
            "multi_photo_gallery": {"count": multi_photo, "pct": fmt_pct(multi_photo, total)},
            "gallery_status": dict(gs_dist),
            "photo_cached": dict(pc_dist),
        },
        "threshold": "n/a (report only)",
        "note": f"gallery_status distribution: {dict(gs_dist)}",
    }


def check_location_intelligence(items: list[dict]) -> dict:
    """Location intelligence confidence/quality distribution."""
    conf_dist: Counter[str] = Counter()
    qual_dist: Counter[str] = Counter()
    has_li = 0
    for x in items:
        li = x.get("location_intelligence", {})
        if isinstance(li, dict) and li:
            has_li += 1
            c = str(li.get("confidence", ""))
            q = str(li.get("quality", ""))
            if c:
                conf_dist[c] += 1
            if q:
                qual_dist[q] += 1

    # Also check geo_quality
    gq_levels = Counter(safe_str(x.get("geo_quality", {}).get("level")) for x in items if isinstance(x.get("geo_quality"), dict))
    # map_point presence
    map_point_count = sum(1 for x in items if x.get("map_point"))

    return {
        "check": "location_intelligence",
        "status": "REPORT",
        "value": {
            "total": len(items),
            "has_location_intelligence": has_li,
            "confidence_distribution": dict(conf_dist),
            "quality_distribution": dict(qual_dist),
            "geo_quality_levels": dict(gq_levels),
            "map_point_presence": map_point_count,
        },
        "threshold": "n/a (report only)",
        "note": f"confidence: {dict(conf_dist)}; quality: {dict(qual_dist)}",
    }


def check_opportunity_analysis(items: list[dict]) -> dict:
    """Opportunity analysis coverage and label distribution."""
    has_oa = 0
    score_labels: Counter[str] = Counter()
    score_values: list[int] = []
    label_dist: Counter[str] = Counter()

    for x in items:
        oa = x.get("opportunity_analysis", {})
        if isinstance(oa, dict) and oa:
            has_oa += 1
            label = str(oa.get("label", ""))
            score = oa.get("score")
            confidence = str(oa.get("confidence", ""))
            if label:
                label_dist[label] += 1
            if score is not None:
                try:
                    score_values.append(int(score))
                except (ValueError, TypeError):
                    pass
            key = str(oa.get("score", "?")) + ":" + label[:25]
            score_labels[key] += 1

    opp_score_count = sum(1 for x in items if x.get("opportunity_score") is not None)

    return {
        "check": "opportunity_analysis",
        "status": "REPORT",
        "value": {
            "total": len(items),
            "has_opportunity_analysis": has_oa,
            "has_opportunity_score": opp_score_count,
            "label_distribution": dict(label_dist.most_common()),
            "score_range": {
                "min": min(score_values) if score_values else None,
                "max": max(score_values) if score_values else None,
                "median": median(score_values) if score_values else None,
            },
            "top_score_labels": dict(score_labels.most_common(10)),
        },
        "threshold": "n/a (report only)",
        "note": f"{has_oa}/{len(items)} have opportunity_analysis",
    }


def check_technical_fragments(items: list[dict]) -> dict:
    """Dedicated check for technical fragment leakage (P2 but reported)."""
    anomalies: list[dict] = []
    for x in items:
        combined = safe_str(x.get("description")) + " " + safe_str(x.get("title"))
        finds = [p for p in TECH_FRAGMENT_PATTERNS if re.search(p, combined, re.I)]
        if finds:
            anomalies.append(
                {
                    "id": x.get("id"),
                    "source": x.get("source"),
                    "patterns": finds,
                    "title": safe_str(x.get("title"))[:100],
                }
            )
    return {
        "check": "technical_fragments",
        "status": "PASS" if not anomalies else "WARN",
        "value": {
            "count": len(anomalies),
            "items": anomalies[:20],
        },
        "threshold": "0 expected",
        "note": f"{len(anomalies)} listings with tech fragments" if anomalies else "clean",
    }


def build_anomalies(items: list[dict]) -> list[dict]:
    """Build top-20 anomaly list across all dimensions."""
    anomalies: list[dict] = []

    for x in items:
        id_ = x.get("id", "?")
        source = x.get("source", "?")
        reasons: list[str] = []

        # Missing source_url (url)
        if not safe_str(x.get("url")):
            reasons.append("missing source_url")

        # Missing or very short description
        desc = safe_str(x.get("description"))
        if not desc:
            reasons.append("empty description")
        elif len(desc) < 30:
            reasons.append(f"very short description ({len(desc)} chars)")

        # Missing critical fields
        if x.get("price") is None:
            reasons.append("no price")
        if x.get("city") is None:
            reasons.append("no city")
        if x.get("surface") is None:
            reasons.append("no surface")

        # Photo issues
        if not x.get("image_url"):
            reasons.append("no image_url")

        if x.get("gallery_status") == "missing":
            reasons.append("gallery missing")

        # Location intelligence low confidence
        li = x.get("location_intelligence", {})
        if isinstance(li, dict):
            try:
                conf = float(li.get("confidence", 1))
                if conf < 0.5:
                    reasons.append(f"low location confidence ({conf})")
            except (ValueError, TypeError):
                pass

        # Furnished but no evidence
        furnished = x.get("furnished", "")
        if furnished in ("meuble", "non_meuble"):
            da = x.get("description_analysis", {})
            if not isinstance(da, dict):
                reasons.append(f"furnished='{furnished}' but no description_analysis")
            else:
                ps = da.get("property_state", {})
                if not isinstance(ps, dict) or not ps.get("furnished"):
                    reasons.append(f"furnished='{furnished}' but no property_state.furnished")

        if reasons:
            anomalies.append(
                {
                    "id": id_,
                    "source": source,
                    "title": safe_str(x.get("title"))[:100],
                    "reasons": reasons,
                    "severity": len(reasons),
                }
            )

    # Sort by number of reasons descending
    anomalies.sort(key=lambda a: (-a["severity"], a["id"]))
    return anomalies[:20]


def generate_checks(items: list[dict], raw_json: str) -> list[dict]:
    """Run all checks and return list of check results."""
    checks: list[dict] = []
    checks.append(check_listings_count(items))
    checks.append(check_json_valid(raw_json))
    checks.append(check_source_distribution(items))
    checks.append(check_field_coverage(items))
    checks.append(
        check_descriptions(items, max_empty=0)
    )
    checks.append(check_photos(items))
    checks.append(check_location_intelligence(items))
    checks.append(check_opportunity_analysis(items))
    checks.append(check_technical_fragments(items))
    return checks


def determine_overall(checks: list[dict]) -> dict:
    """Determine overall verdict. Any P0_FAIL → exit 1."""
    p0_fails = [c for c in checks if c.get("status") == "P0_FAIL"]
    passes = [c for c in checks if c.get("status") == "PASS"]
    warns = [c for c in checks if c.get("status") == "WARN"]
    reports = [c for c in checks if c.get("status") == "REPORT"]

    return {
        "overall": "FAIL" if p0_fails else "PASS",
        "p0_failures": [{"check": c["check"], "note": c["note"]} for c in p0_fails],
        "pass_count": len(passes),
        "warn_count": len(warns),
        "report_count": len(reports),
        "fail_count": len(p0_fails),
    }


def render_markdown(
    overall: dict, checks: list[dict], anomalies: list[dict], app_path: str, generated_at: str
) -> str:
    """Render a human-readable Markdown report."""
    lines: list[str] = []
    lines.append("# Rapport Qualité Sémantique & Sources — Lot C")
    lines.append("")
    lines.append(f"**Généré le:** {generated_at}")
    lines.append(f"**App path:** `{app_path}`")
    lines.append(f"**Verdict:** {'✅ PASS' if overall['overall'] == 'PASS' else '❌ FAIL'}")
    if overall["p0_failures"]:
        lines.append("")
        lines.append("### ⛔ P0 Failures")
        for f in overall["p0_failures"]:
            lines.append(f"- **{f['check']}**: {f['note']}")
    lines.append("")
    lines.append(f"**Pass:** {overall['pass_count']} | **Warn:** {overall['warn_count']} | **Report:** {overall['report_count']} | **Fail:** {overall['fail_count']}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for c in checks:
        status_icon = {"PASS": "✅", "P0_FAIL": "❌", "WARN": "⚠️", "REPORT": "📊"}.get(
            c["status"], "❓"
        )
        lines.append(f"### {status_icon} {c['check']}")
        lines.append(f"**Status:** {c['status']}")
        lines.append(f"**Threshold:** {c['threshold']}")
        lines.append(f"**Note:** {c['note']}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(c["value"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 🏆 Top 20 Anomalies")
    lines.append("")
    if not anomalies:
        lines.append("*Aucune anomalie détectée.*")
    else:
        lines.append("| # | ID | Source | Raisons |")
        lines.append("|---|---|---|---|")
        for i, a in enumerate(anomalies, 1):
            reasons = "; ".join(a["reasons"])
            lines.append(f"| {i} | `{a['id'][:40]}` | {a['source']} | {reasons} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*Rapport généré par `tests/audit_semantic_source_quality_matrix.py`*")
    return "\n".join(lines)


def build_report(
    items: list[dict], raw_json: str, app_path: str, generated_at: str
) -> dict:
    """Build the complete report dict."""
    checks = generate_checks(items, raw_json)
    overall = determine_overall(checks)
    anomalies = build_anomalies(items)
    report: dict = {
        "generated_at": generated_at,
        "app_path": app_path,
        "script": "tests/audit_semantic_source_quality_matrix.py",
        "overall": overall,
        "checks": checks,
        "top_20_anomalies": anomalies,
    }
    return report


def write_outputs(
    report: dict,
    md: str,
    json_out: Path,
    md_out: Path,
) -> None:
    """Write JSON and Markdown outputs."""
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)

    json_out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_out.write_text(md, encoding="utf-8")

    print(f"  JSON → {json_out}")
    print(f"  MD   → {md_out}")


# ── CLI ─────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Lot C — Semantic & Source Quality Matrix Audit"
    )
    ap.add_argument(
        "--app",
        default=os.environ.get("IMMO_APP_PATH", "artifacts/app"),
        help="Path to app artifact directory (default: artifacts/app)",
    )
    ap.add_argument(
        "--json-out",
        default=default_qa_out("semantic_source_quality.json"),
        help="Output path for JSON report",
    )
    ap.add_argument(
        "--md-out",
        default=default_qa_out("semantic_source_quality.md"),
        help="Output path for Markdown report",
    )
    ap.add_argument(
        "--max-empty-descriptions",
        type=int,
        default=0,
        help="Max allowed empty descriptions before P0 (default: 0)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app_path = Path(args.app)
    json_out = Path(args.json_out)
    md_out = Path(args.md_out)
    generated_at = datetime.now(timezone.utc).isoformat()

    print(f"=== Semantic & Source Quality Matrix ===")
    print(f"App: {app_path.resolve()}")
    print(f"JSON: {json_out.resolve()}")
    print(f"MD:   {md_out.resolve()}")
    print()

    # Load
    try:
        items, raw_json = load_listings(app_path)
    except SystemExit as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        # Write a minimal failure report
        fail_report = {
            "generated_at": generated_at,
            "app_path": str(app_path),
            "script": "tests/audit_semantic_source_quality_matrix.py",
            "overall": {"overall": "FAIL", "p0_failures": [{"check": "load", "note": str(exc)}], "pass_count": 0, "warn_count": 0, "report_count": 0, "fail_count": 1},
            "checks": [],
            "top_20_anomalies": [],
        }
        fail_md = f"# Rapport Qualité Sémantique & Sources — Lot C\n\n**Généré le:** {generated_at}\n**App path:** `{app_path}`\n\n## ❌ FATAL\n{exc}\n"
        write_outputs(fail_report, fail_md, json_out, md_out)
        return 1

    print(f"Loaded {len(items)} listings from {app_path / 'listings.json'}")
    print()

    # Build report
    report = build_report(items, raw_json, str(app_path), generated_at)
    md = render_markdown(
        report["overall"],
        report["checks"],
        report["top_20_anomalies"],
        str(app_path),
        generated_at,
    )

    write_outputs(report, md, json_out, md_out)

    # Summary
    print()
    print("=== Results ===")
    print(f"  Overall: {report['overall']['overall']}")
    print(f"  Pass: {report['overall']['pass_count']}, Warn: {report['overall']['warn_count']}, Report: {report['overall']['report_count']}, Fail: {report['overall']['fail_count']}")
    if report["overall"]["p0_failures"]:
        for f in report["overall"]["p0_failures"]:
            print(f"  ❌ {f['check']}: {f['note']}")
    print(f"  Top-20 anomalies: {len(report['top_20_anomalies'])}")

    # Exit code
    if report["overall"]["overall"] == "FAIL":
        print()
        print("P0 FAILURES DETECTED — exiting with code 1")
        return 1

    print()
    print("PASS — no P0 failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())