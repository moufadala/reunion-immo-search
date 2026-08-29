import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
profils = types.ModuleType("profils")
profils.PROFILS = {}
profils.scorer = lambda listing, key: {"score": 0}
sys.modules["profils"] = profils
from export_feed import (
    description_detail_read,
    description_quality_payload,
    normalize_published_at,
)


def test_feed_marks_http_200_empty_as_sparse():
    result = description_quality_payload(
        "",
        {"http_status": 200, "fetched_at": "2026-08-15T10:00:00+00:00"},
    )
    assert result["status"] == "fetched_sparse"
    assert result["markers"] == ["empty"]


def test_feed_marks_expand_prompt_and_synthetic_text_as_sparse():
    result = description_quality_payload(
        "Annonce SeLoger collectee par CDP. Voir plus",
        {"http_status": 200, "fetched_at": "2026-08-15T10:00:00Z"},
    )
    assert result["status"] == "fetched_sparse"
    assert {"expand_prompt", "synthetic_fallback"} <= set(result["markers"])


def test_feed_exposes_complete_description_evidence():
    text = "Appartement familial lumineux avec trois chambres, varangue, parking et une grande cuisine equipee."
    fresh_seen = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    result = description_quality_payload(
        text,
        {
            "http_status": 200,
            "fetched_at": fresh_seen,
            "description_full_text_evidence": 1,
        },
    )
    assert result["status"] == "fetched_complete"
    assert result["length"] == len(text)
    assert len(result["sha256"]) == 64
    assert result["full_text_evidence"] is True


def test_http_200_and_long_text_do_not_claim_detail_read_without_full_text_evidence():
    text = "Appartement familial lumineux avec trois chambres, varangue et parking securise."
    fresh_seen = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    detail = {"http_status": 200, "fetched_at": fresh_seen}
    quality = description_quality_payload(text, detail)

    assert quality["status"] == "fetched_complete"
    assert quality["full_text_evidence"] is False
    assert description_detail_read(detail, quality) is False


def test_verified_structural_detail_is_counted_as_read():
    text = "Studio meuble, libre immediatement."
    detail = {
        "http_status": 200,
        "fetched_at": "2026-08-15T10:00:00Z",
        "description_full_text_evidence": 1,
    }
    quality = description_quality_payload(text, detail)

    assert quality["status"] == "source_short_complete"
    assert description_detail_read(detail, quality) is True


def test_relative_published_date_is_anchored_to_first_seen():
    assert normalize_published_at(
        "il y a 9 h", "2026-08-15T10:00:00Z"
    ) == "2026-08-15T01:00:00+00:00"
    assert normalize_published_at(
        "il y a 1 sem", "2026-08-15T10:00:00Z"
    ) == "2026-08-08T10:00:00+00:00"


def test_invalid_epoch_and_future_publication_dates_are_hidden():
    assert normalize_published_at("1970-01-01", "2026-08-15T10:00:00Z") is None
    assert normalize_published_at("2099-01-01", "2026-08-15T10:00:00Z") is None
