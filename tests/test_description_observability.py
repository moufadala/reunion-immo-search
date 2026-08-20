from datetime import datetime, timedelta, timezone

from src.description_observability import (
    assess_description,
    audit_descriptions,
    select_description,
)


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


def test_http_200_with_short_text_is_not_complete_without_full_text_evidence():
    text = "Studio meuble, libre immediatement."
    observation = assess_description(
        text, http_status=200, attempted_at=NOW, extractor_version="v3", now=NOW
    )

    assert observation.status == "fetched_sparse"
    assert observation.extraction_succeeded is False
    assert observation.full_text_evidence is False


def test_http_200_with_verified_short_source_text_is_explained_complete():
    text = "Studio meuble, libre immediatement."
    observation = assess_description(
        text,
        http_status=200,
        attempted_at=NOW,
        extractor_version="v3",
        full_text_evidence=True,
        now=NOW,
    )

    assert observation.status == "source_short_complete"
    assert observation.extraction_succeeded is True
    assert observation.succeeded_at == NOW
    assert observation.content_state == "source"
    assert observation.markers == ("too_short",)
    assert observation.full_text_evidence is True


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
    assert observation.content_state == "stale"


def test_source_synthetic_and_missing_states_are_explicit():
    source = assess_description(
        "Appartement lumineux avec trois chambres, une varangue, un parking et une cuisine equipee.",
        http_status=200,
        attempted_at=NOW,
        extractor_version="v4",
        now=NOW,
    )
    synthetic = assess_description(
        "Description non fournie par la source.",
        http_status=200,
        attempted_at=NOW,
        extractor_version="v4",
        now=NOW,
    )
    missing = assess_description(
        "", http_status=200, attempted_at=NOW, extractor_version="v4", now=NOW
    )

    assert source.content_state == "source"
    assert synthetic.content_state == "synthetic"
    assert missing.content_state == "missing"


def test_empty_or_synthetic_attempt_never_replaces_complete_existing_description():
    existing = (
        "Appartement T4 lumineux avec trois chambres, grande varangue, parking securise "
        "et cuisine equipee dans une residence calme."
    )
    for candidate in ("", "Description non fournie par la source."):
        attempt = assess_description(
            candidate,
            http_status=200,
            attempted_at=NOW,
            extractor_version="adrezio-v2",
            now=NOW,
        )
        selection = select_description(existing, candidate, candidate_observation=attempt, now=NOW)

        assert selection.text == existing
        assert selection.kept_existing is True
        assert selection.content_state == "source"


def test_longer_complete_source_description_replaces_short_existing_text():
    candidate = (
        "Maison familiale avec quatre chambres, jardin arbore, garage ferme, varangue "
        "et une grande piece de vie traversante proche des commodites."
    )
    attempt = assess_description(
        candidate,
        http_status=200,
        attempted_at=NOW,
        extractor_version="zimo-v3",
        now=NOW,
    )

    selection = select_description("Maison T5.", candidate, candidate_observation=attempt, now=NOW)

    assert selection.text == candidate
    assert selection.kept_existing is False
    assert selection.content_state == "source"


def test_verified_detail_replaces_longer_visibly_truncated_existing_text():
    existing = (
        "Appartement avec trois chambres, une varangue, deux parkings, une cuisine "
        "equipee et de nombreux rangements. Voir plus"
    )
    candidate = (
        "Appartement avec trois chambres, une varangue et deux places de parking."
    )
    attempt = assess_description(
        candidate,
        http_status=200,
        attempted_at=NOW,
        extractor_version="detail-v3",
        full_text_evidence=True,
        now=NOW,
    )

    selection = select_description(
        existing, candidate, candidate_observation=attempt, now=NOW
    )

    assert len(candidate) < len(existing)
    assert selection.text == candidate
    assert selection.kept_existing is False
    assert selection.reason == "verified_source_replaces_truncated_existing"
    assert selection.full_text_evidence is True


def test_adrezio_zimo_audit_records_attempt_length_and_hash():
    rows = [
        {
            "source_site": "adrezio",
            "source_id": "a1",
            "description_full": "Description courte",
            "description_attempted_at": NOW.isoformat(),
            "description_attempt_status": "fetched_sparse",
            "description_attempt_length": 18,
            "description_attempt_sha256": "a" * 64,
        },
        {
            "source_site": "zimo",
            "source_id": "z1",
            "description_full": "",
            "description_attempted_at": None,
        },
        {"source_site": "citya", "source_id": "c1", "description_full": "ignore"},
    ]

    report = audit_descriptions(rows, sources=("adrezio", "zimo"), minimum_length=80)

    assert report["summary"]["targeted"] == 2
    assert report["summary"]["short"] == 2
    assert report["summary"]["attempted"] == 1
    assert report["summary"]["unattempted"] == 1
    assert report["summary"]["unexplained"] == 1
    assert report["items"][0]["attempt_length"] == 18
    assert report["items"][0]["attempt_sha256"] == "a" * 64


def test_default_audit_covers_all_portals_and_gates_only_unexplained_rows():
    rows = [
        {
            "source_site": "adrezio",
            "source_id": "a1",
            "description_full": "Texte court mais reel.",
            "description_content_state": "source",
            "description_attempted_at": NOW.isoformat(),
            "description_full_text_evidence": 1,
            "description_attempt_status": "source_short_complete",
            "description_attempt_length": 22,
        },
        {
            "source_site": "citya",
            "source_id": "c1",
            "description_full": "",
            "description_content_state": "missing",
            "description_attempted_at": NOW.isoformat(),
            "description_attempt_status": "blocked",
            "http_status": 403,
        },
        {
            "source_site": "orpi",
            "source_id": "o1",
            "description_full": "Description source suffisamment longue pour etre consideree comme complete sans relire le detail.",
            "description_content_state": "source",
        },
        {
            "source_site": "zimo",
            "source_id": "z1",
            "description_full": "Description non fournie par la source.",
            "description_content_state": "synthetic",
        },
    ]

    report = audit_descriptions(rows, minimum_length=80)

    assert report["sources"] == ["adrezio", "citya", "orpi", "zimo"]
    assert report["summary"]["targeted"] == 4
    assert report["summary"]["source_short_complete"] == 1
    assert report["summary"]["blocked"] == 1
    assert report["summary"]["complete_source"] == 1
    assert report["summary"]["synthetic"] == 1
    assert report["summary"]["unattempted"] == 1
    assert report["summary"]["unexplained"] == 1
    assert report["items"][0]["explanation"] == "source_short_complete"
    assert report["items"][1]["explanation"] == "blocked"
    assert report["items"][2]["explanation"] == "source_complete"
    assert report["items"][3]["explanation"] == "unattempted"


def test_audit_derives_synthetic_state_and_rejects_inconsistent_success_metadata():
    rows = [
        {
            "source_site": "seloger",
            "source_id": "s1",
            "description_full": "Description non fournie par la source.",
            "description_attempted_at": NOW.isoformat(),
            "description_attempt_status": "fetched_sparse",
            "description_attempt_length": 39,
        },
        {
            "source_site": "citya",
            "source_id": "c1",
            "description_full": "Texte court",
            "description_attempted_at": NOW.isoformat(),
            "description_attempt_status": "fetched_complete",
            "description_attempt_length": 11,
        },
        {
            "source_site": "orpi",
            "source_id": "o1",
            "description_full": "",
            "description_attempted_at": NOW.isoformat(),
            "description_attempt_status": "source_short_complete",
        },
    ]

    report = audit_descriptions(rows, minimum_length=80)
    by_id = {item["source_id"]: item for item in report["items"]}

    assert by_id["s1"]["content_state"] == "synthetic"
    assert by_id["s1"]["explanation"] == "synthetic_response"
    assert by_id["c1"]["explanation"] == "unexplained"
    assert by_id["o1"]["explanation"] == "unexplained"
    assert report["summary"]["synthetic"] == 1
    assert report["summary"]["unexplained"] == 2


def test_unverified_longer_candidate_does_not_replace_verified_existing_detail():
    existing = (
        "Appartement avec trois chambres, une varangue et deux places de parking."
    )
    candidate = (
        "Appartement avec trois chambres, une varangue et deux places de parking. "
        "Consultez aussi toutes les annonces et les services de notre agence immobiliere."
    )
    attempt = assess_description(
        candidate,
        http_status=200,
        attempted_at=NOW,
        extractor_version="meta-v1",
        full_text_evidence=False,
        now=NOW,
    )

    selection = select_description(
        existing, candidate, candidate_observation=attempt,
        existing_full_text_evidence=True, now=NOW,
    )

    assert selection.text == existing
