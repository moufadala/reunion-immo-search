from scripts.export_feed import (
    excluded_quartier_from_description,
    excluded_quartier_from_field,
)


def test_only_providence_and_saint_francois_are_excluded_districts():
    for district in (
        "Bellepierre",
        "Montgaillard",
        "La Montagne",
        "Bas de la Rivière",
    ):
        assert excluded_quartier_from_field(district) is None
        assert (
            excluded_quartier_from_description(
                f"Appartement {district}",
                f"Appartement situé à {district}",
            )
            is None
        )

    assert excluded_quartier_from_field("La Providence") == "Providence"
    assert excluded_quartier_from_field("Saint François") == "Saint-François"
