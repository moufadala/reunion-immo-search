import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
profils = types.ModuleType("profils")
profils.PROFILS = {}
profils.scorer = lambda listing, key: {"score": 0}
sys.modules["profils"] = profils
from export_feed import description_quality_payload, normalize_published_at


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
    result = description_quality_payload(text, {}, "2026-08-15T10:00:00Z")
    assert result["status"] == "fetched_complete"
    assert result["length"] == len(text)
    assert len(result["sha256"]) == 64


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
