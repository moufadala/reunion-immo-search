from scripts.export_feed import reconciliation_product_payload


def _item(identity, **values):
    base = {
        "id": identity,
        "title": "T3",
        "rent": 1200,
        "surface": 70,
        "commune": "Saint-Denis",
        "description": "Description source complète",
        "image": "/thumbs/a.webp",
        "images": ["/thumbs/a.webp"],
    }
    base.update(values)
    return base


def test_product_reconciliation_accounts_for_exclusions_and_hidden_identity():
    eligible = [_item("a"), _item("b"), _item("c")]
    visible = [_item("a"), _item("c")]
    visible[0]["also_on"] = [
        {"id": "a", "source": "ofim", "url": "https://example/a"},
        {"id": "b", "source": "zimo", "url": "https://example/b"},
    ]

    report = reconciliation_product_payload(
        5,
        {
            "x": "surface_below_65",
            "y": "commune_outside_scope",
        },
        eligible,
        visible,
    )

    assert report["active_input"] == 5
    assert sum(report["policy_exclusions"].values()) == 2
    assert report["excluded_ids"] == {
        "x": "surface_below_65",
        "y": "commune_outside_scope",
    }
    assert report["eligible"] == 3
    assert report["dedup_hidden"] == 1
    assert report["visible"] == 2
    assert report["also_on_ids"] == ["b"]


def test_product_reconciliation_leaves_missing_visible_content_unexplained():
    visible = [_item("a", description="", image=None, images=[])]

    report = reconciliation_product_payload(
        visible_items=visible,
        eligible_items=visible,
        active_input=1,
        excluded_ids={},
    )

    assert report["fields"]["missing_description"] == 1
    assert report["fields"]["missing_photo"] == 1
    assert "missing_description" not in report["field_explanations"]
    assert "missing_photo" not in report["field_explanations"]
    assert "missing_description" not in report["field_explanation_reasons"]
    assert "missing_photo" not in report["field_explanation_reasons"]


def test_product_reconciliation_records_bounded_local_photo_degradation(monkeypatch):
    monkeypatch.setenv("IMMO_MAX_ACTIVES_SANS_PHOTO_RATIO", "0.08")
    visible = [_item(str(i)) for i in range(20)]
    visible[0].update(image=None, images=[])

    report = reconciliation_product_payload(20, {}, visible, visible)

    assert report["field_explanations"]["missing_photo"] == 1
    assert report["degradations"]["missing_photo"] == {
        "reason": "remote_source_image_not_cached",
        "count": 1,
        "ids": ["0"],
        "visible": 20,
        "ratio": 0.05,
        "max_ratio": 0.08,
        "public_image_policy": "local_only",
    }


def test_product_reconciliation_does_not_explain_photo_gap_over_budget(monkeypatch):
    monkeypatch.setenv("IMMO_MAX_ACTIVES_SANS_PHOTO_RATIO", "0.04")
    visible = [_item(str(i)) for i in range(20)]
    visible[0].update(image=None, images=[])

    report = reconciliation_product_payload(20, {}, visible, visible)

    assert "missing_photo" not in report["field_explanations"]
    assert report["degradations"] == {}


def test_product_reconciliation_carries_exact_exclusion_policy_inputs():
    evidence = {
        "ofim:x": {
            "surface": 80,
            "rent": 1200,
            "commune": "Sainte-Suzanne",
            "title": "Maison",
        }
    }

    report = reconciliation_product_payload(
        1, {"ofim:x": "commune_outside_scope"}, [], [], evidence
    )

    assert report["exclusion_policy_inputs"] == evidence
