from src.public_feed_dedup import deduplicate_public_feed


def listing(id, **values):
    base = {"id": id, "source": id.split(":")[0], "url": f"https://example.test/{id}", "commune": "Saint-Denis", "type": "Appartement", "rooms": 3, "rent": 1100, "surface": 70, "title": "Résidence Les Flamboyants T3 avec varangue", "description": "Grand appartement lumineux dans la résidence Les Flamboyants avec varangue et parking sécurisé. " * 2, "images": []}
    base.update(values)
    return base


def test_strong_cross_portal_duplicate_keeps_one_card_and_all_links() -> None:
    items = [listing("a:1", address="4 rue des Flamboyants", images=["unique.jpg"]), listing("b:2", address="4 rue des Flamboyants", images=["unique.jpg"])]
    visible, report = deduplicate_public_feed(items)
    assert len(visible) == 1
    assert {x["source"] for x in visible[0]["also_on"]} == {"a", "b"}
    assert report["hidden_duplicates"] == 1


def test_similar_numbers_without_strong_evidence_remain_visible() -> None:
    items = [listing("a:1", title="Appartement T3", description="Premier logement"), listing("b:2", title="Appartement T3", description="Deuxième logement")]
    visible, report = deduplicate_public_feed(items)
    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0


def test_contradictory_addresses_are_never_merged() -> None:
    items = [listing("a:1", address="1 rue A", residence="Les Flamboyants"), listing("b:2", address="9 rue B", residence="Les Flamboyants")]
    visible, _ = deduplicate_public_feed(items)
    assert len(visible) == 2
