from src.publication_policy import evaluate_publication


def row(**values):
    base = {"surface": 65, "rent": 1700, "commune": "Saint-Denis", "quartier": "Le Chaudron"}
    base.update(values)
    return base


def test_numeric_boundaries_are_global_and_inclusive() -> None:
    assert evaluate_publication(row(surface=65, rent=1700)).eligible
    assert evaluate_publication(row(surface=64.99)).reason == "surface_below_65"
    assert evaluate_publication(row(rent=1700.01)).reason == "rent_above_1700"


def test_unknown_or_invalid_numbers_are_not_published() -> None:
    assert evaluate_publication(row(surface=None)).reason == "surface_missing_or_invalid"
    assert evaluate_publication(row(rent="NC")).reason == "rent_missing_or_invalid"


def test_schema_aliases_are_supported() -> None:
    decision = evaluate_publication({"surface_m2": "65", "rent_eur": "1700", "city": "Sainte-Marie"})
    assert decision.eligible


def test_excluded_saint_denis_quartiers_handle_spelling_variants() -> None:
    for quartier in ["Providence", "La Providence", "Saint-François", "Saint Francois", "St-François"]:
        assert not evaluate_publication(row(quartier=quartier)).eligible
        assert not evaluate_publication(row(commune="St-Denis", quartier=quartier)).eligible


def test_quartier_words_do_not_exclude_other_cities_or_nearby_landmarks() -> None:
    assert evaluate_publication(row(commune="Sainte-Marie", quartier="Providence")).eligible
    assert evaluate_publication(row(quartier=None, description="À 10 minutes de Providence")).eligible
