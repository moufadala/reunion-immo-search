from __future__ import annotations

import scripts.postflight_public_contract as postflight
from scripts.export_feed import reconciliation_product_payload
from src.pipeline_reconciliation import reconcile_pipeline


def _visible(identity: str, **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": identity,
        "title": "Appartement T3",
        "rent": 1200,
        "surface": 70,
        "commune": "Saint-Denis",
        "description": "Texte source disponible.",
        "image": "/thumbs/a.webp",
        "images": ["/thumbs/a.webp"],
    }
    item.update(overrides)
    return item


def _reconcile_product(product: dict[str, object]) -> dict[str, object]:
    return reconcile_pipeline(
        {
            "run_id": "run-content-contract",
            "sources": [],
            "database": {
                "total": 1,
                "active": 1,
                "inactive": 0,
                "new": 0,
                "disappeared": 0,
                "reappeared": 0,
            },
            "product": product,
            "fields": product["fields"],
            "field_explanations": product["field_explanations"],
        }
    )


def test_reconciliation_rejects_visible_missing_description_and_photo() -> None:
    visible = [_visible("portal:1", description="  ", image=" ", images=[None, ""])]
    product = reconciliation_product_payload(1, {}, visible, visible)

    result = _reconcile_product(product)

    assert "fields: visible missing_description=1" in result["errors"]
    assert "fields: visible missing_photo=1" in result["errors"]


def test_nonempty_sparse_or_stale_description_is_not_a_missing_field() -> None:
    visible = [
        _visible(
            "portal:1",
            description="Bref",
            description_quality={"status": "stale", "reason": "old observation"},
        )
    ]

    product = reconciliation_product_payload(1, {}, visible, visible)

    assert product["fields"]["missing_description"] == 0


def test_postflight_visible_content_contract_checks_presence_not_quality_label() -> None:
    checker = getattr(postflight, "visible_content_violations", None)
    assert checker is not None, "postflight must expose the visible-content gate used by main"

    violations = checker(
        [
            _visible("missing-description", description=""),
            _visible("missing-photo", image=None, images=[]),
            _visible(
                "sparse-but-visible",
                description="Bref",
                description_quality={"status": "stale"},
            ),
        ]
    )

    assert violations == {
        "missing_description": ["missing-description"],
        "missing_photo": ["missing-photo"],
    }
    assert "visible_content_violations" in postflight.main.__code__.co_names


def test_postflight_rejects_only_proven_incomplete_description_states() -> None:
    checker = postflight.visible_content_violations
    listings = [
        _visible(
            "expand",
            description="Voir la suite",
            description_quality={"status": "fetched_sparse", "markers": ["expand_prompt"]},
        ),
        _visible(
            "ellipsis",
            description="Appartement avec vue...",
            description_quality={"markers": ["truncated_ellipsis"]},
        ),
        _visible(
            "synthetic",
            description="Appartement T3",
            description_quality={"markers": ["synthetic_fallback"]},
        ),
        _visible("empty-status", description="Texte conservé", description_quality={"status": "empty"}),
        _visible("honest-stale", description="Bref", description_quality={"status": "stale"}),
    ]

    violations = checker(listings)


    assert violations["invalid_description"] == ["expand", "ellipsis", "synthetic", "empty-status"]
