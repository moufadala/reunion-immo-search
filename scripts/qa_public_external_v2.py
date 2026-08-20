#!/usr/bin/env python3
"""Blocking external boundary gate for the two published immo hostnames.

This intentionally does not inspect application content and never accepts
credentials. A successful anonymous HTTP 401 proves that DNS resolved, the
default Python trust store accepted the HTTPS certificate, and BasicAuth still
protects the application boundary.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_PRIMARY_HOST = "immo.srv1723523.hstgr.cloud"
DEFAULT_ALTERNATE_HOST = "immo.148.230.103.174.sslip.io"
HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)

Resolver = Callable[..., Iterable[tuple[Any, ...]]]
Opener = Callable[..., Any]


def _validated_hosts(hosts: Iterable[str]) -> tuple[str, str]:
    values = tuple(str(host or "").strip().rstrip(".") for host in hosts)
    if (
        len(values) != 2
        or not all(values)
        or values[0].lower() == values[1].lower()
        or not all(HOSTNAME_RE.fullmatch(host) for host in values)
    ):
        raise ValueError("external gate requires two distinct hostnames")
    return values[0], values[1]


def _addresses(host: str, resolver: Resolver) -> list[str]:
    records = resolver(host, 443, type=socket.SOCK_STREAM)
    addresses = sorted(
        {
            str(record[4][0])
            for record in records
            if len(record) >= 5 and record[4] and record[4][0]
        }
    )
    if not addresses:
        raise OSError("resolver returned no address")
    return addresses


def _tls_error(error: BaseException) -> BaseException | None:
    candidate: BaseException = error
    if isinstance(error, URLError) and isinstance(error.reason, BaseException):
        candidate = error.reason
    return candidate if isinstance(candidate, ssl.SSLError) else None


def check_host(
    host: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
    opener: Opener = urlopen,
    timeout: float = 15.0,
    context_factory: Callable[[], ssl.SSLContext] = ssl.create_default_context,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "host": host,
        "ok": False,
        "dns": {"ok": False, "addresses": []},
        "tls": {"ok": False},
        "http": {"ok": False, "status": None},
        "errors": [],
    }
    try:
        result["dns"] = {"ok": True, "addresses": _addresses(host, resolver)}
    except Exception as error:
        result["errors"].append(
            f"DNS {host} failed: {type(error).__name__}: {error}"
        )
        return result

    request = Request(
        f"https://{host}/",
        headers={"User-Agent": "reunion-immo-public-external-gate-v2"},
    )
    status: int | None = None
    response = None
    try:
        response = opener(request, timeout=timeout, context=context_factory())
        raw_status = getattr(response, "status", None)
        if raw_status is None:
            raw_status = response.getcode()
        status = int(raw_status)
        result["tls"] = {"ok": True}
    except HTTPError as error:
        status = int(error.code)
        result["tls"] = {"ok": True}
    except Exception as error:
        tls_error = _tls_error(error)
        if tls_error is not None:
            result["errors"].append(
                f"TLS {host} invalid: {type(tls_error).__name__}: {tls_error}"
            )
        else:
            result["errors"].append(
                f"HTTPS {host} failed: {type(error).__name__}: {error}"
            )
        return result
    finally:
        if response is not None:
            response.close()

    result["http"] = {"ok": status == 401, "status": status}
    if status != 401:
        result["errors"].append(
            f"HTTP {host} expected anonymous HTTP 401, got {status}"
        )
        return result
    result["ok"] = True
    return result


def run_gate(
    hosts: Iterable[str],
    *,
    resolver: Resolver = socket.getaddrinfo,
    opener: Opener = urlopen,
    timeout: float = 15.0,
    context_factory: Callable[[], ssl.SSLContext] = ssl.create_default_context,
) -> dict[str, Any]:
    checked_hosts = _validated_hosts(hosts)
    results = [
        check_host(
            host,
            resolver=resolver,
            opener=opener,
            timeout=timeout,
            context_factory=context_factory,
        )
        for host in checked_hosts
    ]
    return {"ok": all(item["ok"] for item in results), "hosts": results}


def _write_json(path: str, report: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate DNS, trusted TLS and anonymous HTTP 401 on both immo hostnames"
    )
    parser.add_argument(
        "--primary-host",
        default=os.environ.get("IMMO_HOSTNAME", DEFAULT_PRIMARY_HOST),
    )
    parser.add_argument(
        "--alternate-host",
        default=os.environ.get("IMMO_ALT_HOSTNAME", DEFAULT_ALTERNATE_HOST),
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)

    try:
        report = run_gate(
            (args.primary_host, args.alternate_host), timeout=args.timeout
        )
    except ValueError as error:
        report = {"ok": False, "hosts": [], "errors": [str(error)]}

    for item in report.get("hosts", []):
        if item["ok"]:
            addresses = ",".join(item["dns"]["addresses"])
            print(f"PUBLIC_EXTERNAL_V2_OK host={item['host']} dns={addresses} tls=valid http=401")
        else:
            for error in item["errors"]:
                print(f"PUBLIC_EXTERNAL_V2_FAIL {error}", file=sys.stderr)
    for error in report.get("errors", []):
        print(f"PUBLIC_EXTERNAL_V2_FAIL {error}", file=sys.stderr)
    if args.json_out:
        _write_json(args.json_out, report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
