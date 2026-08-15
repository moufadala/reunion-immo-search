from datetime import datetime, timedelta, timezone

from src.description_observability import assess_description


NOW = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)


def test_unattempted_description_has_no_success_metadata():
    observation = assess_description(None, extractor_version="v3", now=NOW)

    assert observation.status == "not_attempted"
    assert observation.attempted_at is None
    assert observation.succeeded_at is None
    assert observation.length == 0
    assert observation.sha256 is None


def test_http_200_with_empty_body_is_sparse_not_complete():
    observation = assess_description(
        "   ", http_status=200, attempted_at=NOW, extractor_version="v3", now=NOW
    )

    assert observation.status == "fetched_sparse"
    assert observation.extraction_succeeded is False
    assert "empty" in observation.markers


def test_complete_description_preserves_length_hash_version_and_timestamps():
    text = "Appartement lumineux avec trois chambres, varangue, parking et cuisine equipee dans une residence calme."
    observation = assess_description(
        text, http_status=200, attempted_at=NOW, extractor_version="seloger-v4", now=NOW
    )

    assert observation.status == "fetched_complete"
    assert observation.length == len(text)
    assert len(observation.sha256 or "") == 64
    assert observation.extractor_version == "seloger-v4"
    assert observation.attempted_at == NOW
    assert observation.succeeded_at == NOW
    assert observation.extraction_succeeded is True


def test_truncation_and_synthetic_markers_force_sparse():
    truncated = assess_description(
        "Bel appartement avec vue mer. Voir plus",
        http_status=200,
        attempted_at=NOW,
        extractor_version="v1",
        now=NOW,
    )
    synthetic = assess_description(
        "Annonce SeLoger collectee par CDP avec les informations disponibles.",
        http_status=200,
        attempted_at=NOW,
        extractor_version="v1",
        now=NOW,
    )

    assert truncated.status == "fetched_sparse"
    assert "expand_prompt" in truncated.markers
    assert synthetic.status == "fetched_sparse"
    assert "synthetic_fallback" in synthetic.markers


def test_blocked_and_error_are_distinct_failures():
    blocked = assess_description(
        None, http_status=403, attempted_at=NOW, extractor_version="v2", blocked=True, now=NOW
    )
    errored = assess_description(
        None, attempted_at=NOW, extractor_version="v2", error="timeout", now=NOW
    )

    assert blocked.status == "blocked"
    assert blocked.extraction_succeeded is False
    assert errored.status == "error"
    assert errored.error == "timeout"


def test_old_success_is_stale_without_losing_content_evidence():
    old = NOW - timedelta(days=10)
    text = "Maison familiale avec quatre chambres, jardin arbore, garage ferme et grande piece de vie traversante."
    observation = assess_description(
        text,
        http_status=200,
        attempted_at=old,
        succeeded_at=old,
        extractor_version="v2",
        now=NOW,
        stale_after=timedelta(days=7),
    )

    assert observation.status == "stale"
    assert observation.length == len(text)
    assert observation.sha256 is not None
    assert observation.succeeded_at == old
