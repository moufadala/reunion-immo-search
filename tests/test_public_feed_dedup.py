from src.public_feed_dedup import deduplicate_public_feed


def listing(id, **values):
    base = {"id": id, "source": id.split(":")[0], "url": f"https://example.test/{id}", "commune": "Saint-Denis", "type": "Appartement", "rooms": 3, "rent": 1100, "surface": 70, "title": "Résidence Les Flamboyants T3 avec varangue", "description": "Grand appartement lumineux dans la résidence Les Flamboyants avec varangue et parking sécurisé. " * 2, "images": []}
    base.update(values)
    return base


def test_strong_cross_portal_duplicate_keeps_one_card_and_all_links() -> None:
    items = [
        listing("a:1", address="4 rue des Flamboyants", images=["common.jpg", "a.jpg"], balcony=True),
        listing("b:2", address="4 rue des Flamboyants", images=["common.jpg", "b.jpg"], elevator=True),
    ]
    visible, report = deduplicate_public_feed(items)
    assert len(visible) == 1
    assert {x["source"] for x in visible[0]["also_on"]} == {"a", "b"}
    assert visible[0]["seen_also_on"] == ["a", "b"]
    assert visible[0]["display_canonical"] is True
    assert visible[0]["canonical_display_id"] == visible[0]["id"]
    assert visible[0]["dedup_decision"] == "canonical"
    assert visible[0]["dedup_group_id"].startswith("dedup:")
    assert report["hidden_duplicates"] == 1
    summary = report["group_summaries"][0]
    assert summary["canonical_display_id"] == visible[0]["id"]
    assert summary["member_count"] == 2
    assert summary["sources"] == ["a", "b"]
    assert summary["reason"] == "same_address_and_photo"
    assert "url" not in summary
    assert set(visible[0]["images"]) == {"common.jpg", "a.jpg", "b.jpg"}
    assert visible[0]["balcony"] is True
    assert visible[0]["elevator"] is True


def test_similar_numbers_without_strong_evidence_remain_visible() -> None:
    items = [listing("a:1", title="Appartement T3", description="Premier logement"), listing("b:2", title="Appartement T3", description="Deuxième logement")]
    visible, report = deduplicate_public_feed(items)
    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0


def test_contradictory_addresses_are_never_merged() -> None:
    items = [listing("a:1", address="1 rue A", residence="Les Flamboyants"), listing("b:2", address="9 rue B", residence="Les Flamboyants")]
    visible, _ = deduplicate_public_feed(items)
    assert len(visible) == 2


def test_real_run_153444_legacy_noncanonical_singletons_are_public_canonicals() -> None:
    # Real visible rows that retained DB-era canonical=false in run 20260815T153444Z.
    items = [
        listing("locamoi:p16e785", source="locamoi", canonical=False,
                title="location appartement saint-denis 3 pi?ces 75 m2 reunion (97400) - 1350 ? / mois",
                rent=1350, surface=75),
        listing("bienici:hektor-CABINETHABILIS-6552", source="bienici", canonical=False,
                title="? Saint-Denis, appartement ? louer pour petite famille",
                rooms=4, rent=1110, surface=86),
        listing("superimmo:x11t77q", source="superimmo", canonical=False,
                title="location maison 70m sainte marie 97438", commune="Sainte-Marie",
                type="Maison", rent=880, surface=70),
    ]

    visible, report = deduplicate_public_feed(items)

    assert len(visible) == 3
    for item in visible:
        assert item["canonical"] is True
        assert item["display_canonical"] is True
        assert item["canonical_display_id"] == item["id"]
        assert item["dedup_decision"] == "canonical"
        assert "also_on" not in item
    assert report["group_summaries"] == []
