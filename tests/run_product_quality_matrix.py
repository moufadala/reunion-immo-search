#!/usr/bin/env python3
"""Unified product-quality runner for Immo Réunion.

Executes the gates declared in tests/product_quality_matrix.json, grouped by
product family and blocking level (P0/P1/P2). The runner is intentionally a thin
orchestrator around existing audits: no secrets, no publication, no mutation of
artifacts/app beyond whatever the existing read-only audits already do.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "tests" / "product_quality_matrix.json"
LEVEL_ORDER = {"P0": 0, "P1": 1, "P2": 2}
TRUNCATE_CHARS = 12_000


def default_qa_out(name: str) -> str:
    """Return a run-scoped temp output path for read-only QA defaults.

    Product QA must not dirty the repository just because someone runs the
    default command. Operators can still pass --json-out/--md-out explicitly
    when they intentionally want durable artifacts.
    """
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return str(Path(tempfile.gettempdir()) / f"immo-qa-{stamp}" / name)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def truncate(text: str, limit: int = TRUNCATE_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    head = limit // 2
    tail = limit - head
    return text[:head] + f"\n...[truncated {omitted} chars]...\n" + text[-tail:]


def resolve_playwright_python() -> str:
    """Return a Python interpreter with Playwright when the project venv exists.

    The system Python on the VPS intentionally has no pip/playwright. Browser-level
    immo audits historically run from /opt/data/labs/browser-use-poc/venv.
    """
    candidates = [
        Path("/opt/data/labs/browser-use-poc/venv/bin/python"),
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def fmt_cmd(command: list[str], app: Path, public: str) -> list[str]:
    values = {
        "app": str(app),
        "public": public.rstrip("/"),
        "python": sys.executable,
        "playwright_python": resolve_playwright_python(),
    }
    return [part.format(**values) for part in command]


def should_block(level: str, fail_level: str) -> bool:
    return LEVEL_ORDER[level] <= LEVEL_ORDER[fail_level]


def load_matrix(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        matrix = json.load(f)
    gates = matrix.get("gates")
    if not isinstance(gates, list) or not gates:
        raise SystemExit(f"Matrix invalide: aucun gate dans {path}")
    for gate in gates:
        for key in ("id", "family", "level", "command"):
            if key not in gate:
                raise SystemExit(f"Matrix invalide: gate sans {key}: {gate!r}")
        if gate["level"] not in LEVEL_ORDER:
            raise SystemExit(f"Niveau inconnu pour {gate['id']}: {gate['level']}")
    return matrix


def run_gate(gate: dict[str, Any], app: Path, public: str) -> dict[str, Any]:
    command = fmt_cmd(gate["command"], app=app, public=public)
    env = os.environ.copy()
    env.update(
        {
            "IMMO_APP_PATH": str(app),
            "IMMO_PUBLIC_BASE": public.rstrip("/"),
            "IMMO_PUBLIC_URL": public.rstrip("/") + "/",
            "PYTHONUNBUFFERED": "1",
        }
    )
    started = utc_now()
    t0 = time.monotonic()
    timeout = int(gate.get("timeout_seconds") or 300)
    timed_out = False
    try:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        exit_code = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        raw_stdout = exc.stdout or ""
        raw_stderr = exc.stderr or ""
        stdout = raw_stdout.decode("utf-8", errors="replace") if isinstance(raw_stdout, bytes) else raw_stdout
        stderr = raw_stderr.decode("utf-8", errors="replace") if isinstance(raw_stderr, bytes) else raw_stderr
        stderr += f"\nTIMEOUT after {timeout}s"
    duration = round(time.monotonic() - t0, 3)
    return {
        "id": gate["id"],
        "family": gate["family"],
        "level": gate["level"],
        "role": gate.get("role", ""),
        "command": command,
        "exit_code": exit_code,
        "status": "PASS" if exit_code == 0 else "FAIL",
        "timed_out": timed_out,
        "timeout_seconds": timeout,
        "started_at": started,
        "duration_seconds": duration,
        "stdout": truncate(stdout),
        "stderr": truncate(stderr),
    }


def summarize(results: list[dict[str, Any]], fail_level: str) -> dict[str, Any]:
    by_family: dict[str, dict[str, Any]] = {}
    by_level: dict[str, dict[str, int]] = {lvl: {"pass": 0, "fail": 0, "total": 0} for lvl in LEVEL_ORDER}
    blocking_failures: list[str] = []
    for r in results:
        fam = by_family.setdefault(r["family"], {"pass": 0, "fail": 0, "total": 0})
        fam["total"] += 1
        by_level[r["level"]]["total"] += 1
        if r["exit_code"] == 0:
            fam["pass"] += 1
            by_level[r["level"]]["pass"] += 1
        else:
            fam["fail"] += 1
            by_level[r["level"]]["fail"] += 1
            if should_block(r["level"], fail_level):
                blocking_failures.append(r["id"])
    return {
        "status": "PASS" if not blocking_failures else "FAIL",
        "fail_level": fail_level,
        "total": len(results),
        "passed": sum(1 for r in results if r["exit_code"] == 0),
        "failed": sum(1 for r in results if r["exit_code"] != 0),
        "blocking_failures": blocking_failures,
        "by_family": by_family,
        "by_level": by_level,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def md_escape(text: Any) -> str:
    return str(text).replace("|", "\\|")


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# Rapport matrice QA produit — Immo Réunion",
        "",
        f"- Généré: `{payload['generated_at']}`",
        f"- Statut: **{summary['status']}**",
        f"- Seuil bloquant: `{summary['fail_level']}`",
        f"- App: `{payload['inputs']['app']}`",
        f"- Public: `{payload['inputs']['public']}`",
        f"- Gates: {summary['passed']}/{summary['total']} PASS, {summary['failed']} FAIL",
        "",
        "## Synthèse familles",
        "",
    ]
    for family, data in sorted(summary["by_family"].items()):
        lines.append(f"- **{family}**: {data['pass']}/{data['total']} PASS, {data['fail']} FAIL")
    lines.extend(["", "## Synthèse niveaux", ""])
    for level in ("P0", "P1", "P2"):
        data = summary["by_level"][level]
        lines.append(f"- **{level}**: {data['pass']}/{data['total']} PASS, {data['fail']} FAIL")
    if summary["blocking_failures"]:
        lines.extend(["", "## Échecs bloquants", ""])
        for gate_id in summary["blocking_failures"]:
            lines.append(f"- `{gate_id}`")
    lines.extend(["", "## Détail gates", ""])
    for r in payload["results"]:
        lines.extend(
            [
                f"### {r['status']} — `{r['id']}` ({r['family']} / {r['level']})",
                "",
                f"- Commande: `{' '.join(md_escape(x) for x in r['command'])}`",
                f"- Exit code: `{r['exit_code']}`",
                f"- Durée: `{r['duration_seconds']}s`",
                f"- Timeout: `{r['timeout_seconds']}s`" + (" — TIMEOUT" if r.get("timed_out") else ""),
            ]
        )
        if r.get("role"):
            lines.append(f"- Rôle: {r['role']}")
        if r.get("stdout"):
            lines.extend(["", "```text", r["stdout"].rstrip(), "```"])
        if r.get("stderr"):
            lines.extend(["", "```text", r["stderr"].rstrip(), "```"])
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run Immo Réunion product QA matrix")
    ap.add_argument("--app", default="artifacts/app", help="Chemin de l'artefact app local")
    ap.add_argument("--public", default="https://immo.148.230.103.174.sslip.io/", help="URL publique QA")
    ap.add_argument("--json-out", default=default_qa_out("latest.json"), help="Rapport JSON de sortie")
    ap.add_argument("--md-out", default=default_qa_out("latest.md"), help="Rapport Markdown de sortie")
    ap.add_argument("--family", action="append", help="Famille à exécuter; répétable. Défaut: toutes")
    ap.add_argument("--gate", action="append", help="Gate id à exécuter; répétable. Défaut: tous les gates sélectionnés")
    ap.add_argument("--include-parallel", action="store_true", help="Inclut les gates phase=parallel, exclus du run standard")
    ap.add_argument("--fail-level", choices=("P0", "P1", "P2"), default="P0", help="Niveau maximal bloquant")
    ap.add_argument("--matrix", default=str(DEFAULT_MATRIX), help="Manifeste JSON de matrice")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    app = Path(args.app)
    public = args.public.rstrip("/")
    matrix_path = Path(args.matrix)
    matrix = load_matrix(matrix_path)
    families = set(args.family or [])
    gate_ids = set(args.gate or [])
    known_families = {g["family"] for g in matrix["gates"]}
    known_gate_ids = {g["id"] for g in matrix["gates"]}
    unknown = families - known_families
    if unknown:
        raise SystemExit(f"Famille inconnue: {', '.join(sorted(unknown))}. Connues: {', '.join(sorted(known_families))}")
    unknown_gates = gate_ids - known_gate_ids
    if unknown_gates:
        raise SystemExit(f"Gate inconnu: {', '.join(sorted(unknown_gates))}. Connus: {', '.join(sorted(known_gate_ids))}")
    selected = [
        g
        for g in matrix["gates"]
        if (not families or g["family"] in families)
        and (not gate_ids or g["id"] in gate_ids)
        and (args.include_parallel or g.get("phase") != "parallel")
    ]
    if not selected:
        raise SystemExit("Aucun gate sélectionné")

    results: list[dict[str, Any]] = []
    for gate in selected:
        print(f"[{gate['family']}/{gate['level']}] {gate['id']} ...", flush=True)
        result = run_gate(gate, app=app, public=public)
        results.append(result)
        print(f"  -> {result['status']} exit={result['exit_code']} duration={result['duration_seconds']}s", flush=True)

    payload = {
        "generated_at": utc_now(),
        "matrix_path": str(matrix_path),
        "schema_version": matrix.get("schema_version"),
        "inputs": {
            "app": str(app),
            "public": public,
            "family": sorted(families) if families else None,
            "gate": sorted(gate_ids) if gate_ids else None,
            "include_parallel": args.include_parallel,
        },
        "summary": summarize(results, args.fail_level),
        "results": results,
    }
    write_json(Path(args.json_out), payload)
    write_markdown(Path(args.md_out), payload)
    print(f"JSON: {args.json_out}")
    print(f"Markdown: {args.md_out}")
    print(f"STATUS: {payload['summary']['status']}")
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
