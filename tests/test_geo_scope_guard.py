from src.geo_scope_guard import manifest_outside_scope


def test_detects_manifestly_outside_localities_despite_wrong_commune():
    cases = [
        ({"commune": "Saint-Denis", "title": "Appartement T3 - La Saline-les-Hauts"}, "La Saline-les-Hauts"),
        ({"commune": "Sainte-Marie", "address": "12 chemin La Riviere, Saint-Louis"}, "La Riviere / Saint-Louis"),
        ({"commune": "Saint-Denis", "location_label": "Plaine-des-Cafres"}, "Plaine-des-Cafres"),
    ]

    for listing, expected_locality in cases:
        evidence = manifest_outside_scope(listing)
        assert evidence is not None
        assert evidence.locality == expected_locality


def test_does_not_flag_proximity_mentions():
    listings = [
        {"commune": "Saint-Denis", "description": "A 5 km de Saint-Louis, acces rapide."},
        {"commune": "Sainte-Marie", "description": "Proche de La Saline-les-Hauts."},
        {"commune": "Saint-Denis", "description": "A proximite de la Plaine-des-Cafres."},
    ]

    assert all(manifest_outside_scope(listing) is None for listing in listings)


def test_does_not_flag_unrelated_text_or_target_locality():
    assert manifest_outside_scope({"commune": "Saint-Denis", "title": "T3 au Moufia"}) is None
    assert manifest_outside_scope({"commune": "Sainte-Marie", "address": "Duparc"}) is None


def test_structured_location_is_strong_even_if_description_mentions_proximity():
    listing = {
        "commune": "Saint-Denis",
        "location_label": "La Saline-les-Hauts",
        "description": "Proche de Saint-Denis",
    }

    evidence = manifest_outside_scope(listing)
    assert evidence is not None
    assert evidence.field == "location_label"
