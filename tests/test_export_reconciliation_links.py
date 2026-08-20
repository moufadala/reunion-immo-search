from scripts.export_feed import reconciliation_product_payload


def _listing(identity: str, **extra):
    item = {
        "id": identity,
        "title": "T3",
        "rent": 1200,
        "surface": 70,
        "commune": "Saint-Denis",
        "description": "Description source complète",
        "image": "/thumbs/a.webp",
        "images": ["/thumbs/a.webp"],
    }
    item.update(extra)
    return item


def test_missing_eligible_card_without_real_also_on_link_is_unexplained():
    eligible = [_listing("a"), _listing("b")]
    report = reconciliation_product_payload(2, {}, eligible, [_listing("a")])

    assert report["dedup_hidden"] == 0
    assert report["also_on_ids"] == []
    assert report["unexplained_eligible_ids"] == ["b"]


def test_actual_also_on_member_is_the_only_valid_hidden_identity():
    eligible = [_listing("a"), _listing("b")]
    visible = [
        _listing(
            "a",
            also_on=[
                {"id": "a", "source": "ofim", "url": "https://example/a"},
                {"id": "b", "source": "zimo", "url": "https://example/b"},
            ],
        )
    ]
    report = reconciliation_product_payload(2, {}, eligible, visible)

    assert report["dedup_hidden"] == 1
    assert report["also_on_ids"] == ["b"]
    assert report["unexplained_eligible_ids"] == []
