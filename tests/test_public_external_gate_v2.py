from __future__ import annotations

import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from scripts.qa_public_external_v2 import run_gate


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / "scripts" / "immo_daily_public_refresh.sh"


def _resolver(host, port, *, type):
    assert port == 443
    assert type
    suffix = 10 if host == "primary.example" else 11
    return [(2, 1, 6, "", (f"192.0.2.{suffix}", port))]


def _http_401(request, timeout, context):
    assert timeout > 0
    assert isinstance(context, ssl.SSLContext)
    assert request.full_url.startswith("https://")
    assert not request.has_header("Authorization")
    raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)


def test_two_hosts_pass_on_dns_valid_tls_and_anonymous_401():
    report = run_gate(
        ("primary.example", "alternate.example"),
        resolver=_resolver,
        opener=_http_401,
    )

    assert report["ok"] is True
    assert [item["host"] for item in report["hosts"]] == [
        "primary.example",
        "alternate.example",
    ]
    assert all(item["dns"]["ok"] for item in report["hosts"])
    assert all(item["tls"]["ok"] for item in report["hosts"])
    assert all(item["http"] == {"ok": True, "status": 401} for item in report["hosts"])


def test_dns_failure_is_explicit_and_does_not_open_https():
    opened = []

    def resolver(host, port, *, type):
        if host == "primary.example":
            raise OSError("name not found")
        return _resolver(host, port, type=type)

    def opener(*args, **kwargs):
        opened.append(args[0].full_url)
        return _http_401(*args, **kwargs)

    report = run_gate(
        ("primary.example", "alternate.example"), resolver=resolver, opener=opener
    )

    first = report["hosts"][0]
    assert report["ok"] is False
    assert first["dns"]["ok"] is False
    assert "DNS primary.example failed" in first["errors"][0]
    assert all("primary.example" not in url for url in opened)


def test_invalid_certificate_is_reported_as_tls_failure():
    def opener(request, timeout, context):
        error = ssl.SSLCertVerificationError(1, "certificate has expired")
        raise URLError(error)

    report = run_gate(
        ("primary.example", "alternate.example"),
        resolver=_resolver,
        opener=opener,
    )

    first = report["hosts"][0]
    assert report["ok"] is False
    assert first["dns"]["ok"] is True
    assert first["tls"]["ok"] is False
    assert "TLS primary.example invalid" in first["errors"][0]


def test_http_200_is_explicit_privacy_gate_failure():
    class Response:
        status = 200

        def close(self):
            pass

    report = run_gate(
        ("primary.example", "alternate.example"),
        resolver=_resolver,
        opener=lambda request, timeout, context: Response(),
    )

    assert report["ok"] is False
    assert report["hosts"][0]["tls"]["ok"] is True
    assert report["hosts"][0]["http"] == {"ok": False, "status": 200}
    assert "expected anonymous HTTP 401, got 200" in report["hosts"][0]["errors"][0]


@pytest.mark.parametrize(
    "hosts",
    [("primary.example", "primary.example"), ("primary.example", "")],
)
def test_gate_requires_two_distinct_hostnames(hosts):
    with pytest.raises(ValueError, match="two distinct hostnames"):
        run_gate(hosts, resolver=_resolver, opener=_http_401)


def test_daily_pipeline_blocks_after_publish_and_rolls_back_before_keep():
    script = DAILY.read_text(encoding="utf-8")
    publish = script.index("run_step publish_clean_static")
    gate = script.index("run_step public_external_gate_v2")
    keep = script.index("APP_KEEP=1")
    swap_done = script.index("APP_SWAP_DONE=1")
    trap = script[script.index("restore_on_failure()") : script.index("trap restore_on_failure EXIT")]

    assert publish < gate < keep
    assert swap_done < gate
    assert '"$PROJECT/scripts/qa_public_external_v2.py"' in script
    assert 'report_step public_qa bash "$PROJECT/deploy/qa-public.sh"' in script
    assert '[ "${APP_KEEP:-0}" != "1" ]' in trap
    assert 'rollback_public_app.py" --apply' in trap
