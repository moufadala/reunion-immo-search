from scripts.immo_public_monitor import MIN_LISTINGS, feed_contract_errors


def listing(**overrides):
    row = {
        "id": "ok",
        "active": True,
        "surface": 70,
        "rent": 1500,
        "commune": "Saint-Denis",
        "quartier": "Centre",
    }
    row.update(overrides)
    return row


def test_monitor_volume_floor_matches_filtered_public_product():
    assert MIN_LISTINGS <= 194


def test_monitor_rejects_every_publication_policy_violation():
    data = {
        "listings": [
            listing(id="small", surface=64),
            listing(id="expensive", rent=1701),
            listing(id="wrong-city", commune="Sainte-Suzanne"),
            listing(id="providence", quartier="La Providence"),
        ]
    }
    errors = feed_contract_errors(data)
    assert len(errors) == 1
    assert "4 listing(s)" in errors[0]


def test_monitor_accepts_valid_scope_and_matching_movements():
    data = {
        "listings": [listing()],
        "meta": {"marche": {"retirees_7j": 2}},
        "movements": {"disparues_7j": 2},
    }
    assert feed_contract_errors(data) == []


def test_monitor_detects_movement_counter_drift():
    data = {
        "listings": [listing()],
        "meta": {"marche": {"retirees_7j": 1289}},
        "movements": {"disparues_7j": 21},
    }
    errors = feed_contract_errors(data)
    assert any("movement counters disagree" in error for error in errors)

