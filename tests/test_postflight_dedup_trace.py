from scripts.postflight_public_contract import validate_group_summaries


def _card():
    return {
        "id": "ofim_rss:73279",
        "dedup_group_id": "dedup:abc",
        "canonical_display_id": "ofim_rss:73279",
        "dedup_reason": "same_business_reference",
        "also_on": [
            {"id": "ofim_rss:73279", "source": "ofim_rss", "url": "https://example/ofim"},
            {"id": "leboncoin:3243892983", "source": "leboncoin", "url": "https://example/lbc"},
        ],
    }


def _meta():
    return {"groups": 1, "group_summaries": [{
        "group_id": "dedup:abc",
        "canonical_display_id": "ofim_rss:73279",
        "member_count": 2,
        "sources": ["leboncoin", "ofim_rss"],
        "reason": "same_business_reference",
    }]}


def test_group_summary_matches_served_card_exactly():
    assert validate_group_summaries([_card()], _meta()) == []


def test_group_summary_rejects_invented_identity_and_nested_sensitive_data():
    meta = _meta()
    meta["group_summaries"][0]["canonical_display_id"] = "invented"
    assert validate_group_summaries([_card()], meta)
    meta = _meta()
    meta["group_summaries"][0]["details"] = {"url": "https://secret"}
    assert validate_group_summaries([_card()], meta)


def test_group_summary_rejects_duplicate_or_missing_groups():
    meta = _meta()
    meta["group_summaries"].append(dict(meta["group_summaries"][0]))
    assert validate_group_summaries([_card()], meta)
    assert validate_group_summaries([_card()], {"groups": 1, "group_summaries": []})