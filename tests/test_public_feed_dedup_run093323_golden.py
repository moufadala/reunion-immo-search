from pathlib import Path

from src.public_feed_dedup import deduplicate_public_feed

ROOT = Path(__file__).parents[1]



def _row(listing_id: str, title: str, price: int, surface: float, rooms: int,
         description: str, *, city: str = "Saint-Denis", property_type: str = "Maison",
         bedrooms: int | None = None, **extra):
    row = {
        "id": listing_id,
        "source": listing_id.split(":", 1)[0],
        "url": f"https://example.test/{listing_id}",
        "title": title,
        "commune": city,
        "type": property_type,
        "rent": price,
        "surface": surface,
        "rooms": rooms,
        "bedrooms": bedrooms,
        "description": description,
        "images": [],
        "active": True,
        "dedup_group_id": "",
    }
    row.update(extra)
    return row


RUN_093323_PAIRS = [
    (
        _row("bienici:gedeon-33672640", "LOCATION APPART T4", 995, 85, 4,
             "ST DENIS LA BRETAGNE à proximité du Chemin Grand Canal, niveau de la Technopole, au 1er étage d'une petite résidence de 11 logements, ce T4 comprend une entrée avec cuisine équipée US, un salon donnant sur la varangue, 3 chambres climatisées, une salle de bains, un toilette séparé. Libre au 1er septembre. 2 places de voitures sous abri. Surface totale 100M². Loyer 995 euros charges comprises.",
             property_type="Appartement", bedrooms=3),
        _row("zimo:019fec0b-043b-7589-b686-d4264963d029", "LOCATION APPART T4", 995, 85, 4,
             "ST DENIS LA BRETAGNE à proximité du Chemin Grand Canal, niveau de la Technopole, au 1er étage d'une petite résidence de 11 logements, ce T4 comprend une entrée avec cuisine équipée US, un salon donnant sur la varangue, 3 chambres climatisées, une salle de bains, un toilette séparé. Libre au 1er septembre. 2 places de voitures sous abri. Surface totale 100M². Loyer 995 euros charges comprises.",
             property_type="Appartement", bedrooms=3),
    ),
    (
        _row("zimo:019fc807-535e-77be-8742-9546c59b56f6", "Villa 6 pièces 122 m²", 1700, 122, 6,
             "A LOUER VILLA F6 AVEC BELLE VUE DEGAGEE - GRANDE MONTEE SAINTE-MARIE - rue Luc Donat. Belle maison F6 de 122 m² sur 2 niveaux. Séjour climatisé, cuisine équipée, 5 chambres, dressing, salle de bain, varangue de 33 m², terrain de 385 m², jardin, piscine et abri voiture. Loyer mensuel 1700 euros. Référence annonce GDEMONTeE47-ARH.",
             city="Sainte-Marie", bedrooms=5),
        _row("leboncoin:3244001700", "Villa 6 pièces 122 m²", 1700, 122, 6,
             "A LOUER VILLA F6 AVEC BELLE VUE DEGAGEE - GRANDE MONTEE SAINTE-MARIE - rue Luc Donat. Belle maison F6 de 122 m² sur 2 niveaux. Séjour climatisé, cuisine équipée, 5 chambres, dressing, salle de bain, varangue de 33 m², terrain de 385 m², jardin, piscine et abri voiture. Loyer mensuel 1700 euros. Référence annonce GDEMONTeE47-ARH.",
             city="Sainte-Marie", bedrooms=5),
    ),
    (
        _row("zimo:019fe230-86b2-7ee3-8849-dcc64c121259", "Maison f3 hts de ste marie", 1070, 70, 3,
             "Charmante maison T3/4 de 70m2 avec jardin privé et terrasse, résidence sécurisée dans les hauts de Sainte-Marie. Grande terrasse couverte de 30m2 prolongée par un jardin privatif de 50m2. Deux places de parking privatives. Loyer hors charges 950 euros, provisions 120 euros, loyer charges comprises 1070 euros.",
             city="Sainte-Marie", bedrooms=2),
        _row("leboncoin:3246796573", "Maison f3 hts de ste marie", 1070, 70, 3,
             "Charmante maison T3/4 de 70m2 avec jardin privé et terrasse, résidence sécurisée dans les hauts de Sainte-Marie. Grande terrasse couverte de 30m2 prolongée par un jardin privatif de 50m2. Deux places de parking privatives. Loyer hors charges 950 euros, provisions 120 euros, loyer charges comprises 1070 euros.",
             city="Sainte-Marie", bedrooms=2),
    ),
    (
        _row("zimo:019ff018-3d21-72d2-8d15-a364ffc0c043", "Villa 210m2", 1300, 280, 5,
             "À LOUER BELLE VILLA 4 CHAMBRES BOIS DE NÈFLES SAINTE-CLOTILDE. Villa de 210 m² sur terrain de 180 m² comprenant 4 chambres, cuisine aménagée et cuisine extérieure, salle à manger, varangue, salle de bain, garage et deux places de parking. Disponible le 1er octobre. Loyer 1300 euros par mois.",
             bedrooms=4),
        _row("leboncoin:3248194569", "Villa 210m2", 1300, 280, 5,
             "À LOUER – BELLE VILLA 4 CHAMBRES – BOIS DE NÈFLES SAINTE-CLOTILDE. Villa de 210 m² sur terrain de 180 m² comprenant 4 chambres, cuisine aménagée et cuisine extérieure, salle à manger, varangue, salle de bain, garage et deux places de parking. Disponible le 1er octobre. Loyer 1300 euros par mois.",
             bedrooms=4),
    ),
    (
        _row("zimo:019ff138-d84f-73e1-be63-c6cbb39f8bc8", "Appartement F4/5 meublé, étage maison", 1100, 93, 5,
             "Dans les hauts de la Bretagne, à Bellevue. Appartement à l'étage de maison, F4 meublé 93m2 plus mezzanine 50m2 plus terrasse 20m2. Jardin et parking privatif au rez-de-chaussée. Vue dégagée canne et mer. Proche école et bus. Disponible le 1er octobre 2026. 1100 euros eau et entretien pelouse compris.",
             bedrooms=4),
        _row("leboncoin:3248396155", "Appartement F4/5 meublé, étage maison", 1100, 93, 5,
             "Dans les hauts de la Bretagne, à Bellevue. Appartement à l'étage de maison, F4 meublé 93m2 plus mezzanine 50m2 plus terrasse 20m2. Jardin et parking privatif au rez-de-chaussée. Vue dégagée canne et mer. Proche école et bus. Disponible le 1er octobre 2026. 1100 euros entretien pelouse compris.",
             bedrooms=3),
    ),
    (
        _row("zimo:019ff1bf-830e-7ae3-a1ed-ed7f70f636aa", "Location d'une maison superbe bien placée à Bois-Néfles / Saine-Clotilde", 1209, 85, 4,
             "BOIS DE NEFLES, zone très calme et proche de toutes commodités. Maison 3 pièces de 85m² comprenant entrée, séjour avec salle à manger, grande véranda, cuisine avec rangements, 3 chambres dont deux avec penderie, salle de bain avec baignoire, toilette séparée, petit jardin et parking dans la cour et au bord de la route principale.",
             bedrooms=3),
        _row("leboncoin:3248489942", "Location d'une maison superbe bien placée à Bois-Néfles / Saine-Clotilde", 1209, 85, 4,
             "BOIS DE NEFLES, zone très calme et proche de toutes commodités. Maison 3 pièces de 85m² comprenant entrée, séjour avec salle à manger, grande véranda, cuisine avec rangements, 3 chambres dont deux avec penderie, salle de bain avec baignoire, toilette séparée, petit jardin et parking dans la cour et au bord de la route principale.",
             bedrooms=3),
    ),
    (
        _row("zimo:019ff50c-0bc4-7633-9289-c6bb21ecc288", "T3 meublé Saint-Denis 1200 euros", 1200, 80, 3,
             "À louer 1200 euros appartement T3 meublé classé 3 étoiles, proche de toutes commodités. À 5 minutes à pied d'une grande surface, du CHU et du jardin de l'État. À 3 minutes du littoral. Une place de parking sécurisée.",
             property_type="Appartement", bedrooms=2),
        _row("leboncoin:3248751124", "T3 meublé Saint-Denis 1200 euros", 1200, 80, 3,
             "À louer 1200 euros appartement T3 meublé classé « 3 étoiles », proche de toutes commodités. À 5 minutes à pied d’une grande surface, du CHU et du jardin de l’État. À 3 minutes du littoral. Une place de parking sécurisée.",
             property_type="Appartement", bedrooms=2),
    ),
    (
        _row("zimo:019ffc0f-9204-727d-9dd7-f6d9d1f0b967", "Loue Maison Meublée T3 - Sainte Clotilde", 1290, 65, 3,
             "Loue maison de ville entièrement meublée et équipée située rue Foch à Sainte Clotilde. Salon séjour, cuisine, deux chambres climatisées, salle d'eau et wc séparé. Cour avant avec deux places de parking et jardin arrière avec varangue couverte. Quartier calme. Loyer 1250 euros, provision TEOM 40 euros par mois.",
             bedrooms=2),
        _row("leboncoin:3249570727", "Loue Maison Meublée T3 - Sainte Clotilde", 1290, 65, 3,
             "Loue maison de ville entièrement meublée et équipée située rue Foch à Sainte Clotilde ! Salon/séjour, cuisine, deux chambres climatisées, salle d’eau et wc séparé. Cour avant avec deux places de parking et jardin arrière avec varangue couverte. Quartier calme. Loyer 1250 euros, provision TEOM 40 euros/mois.",
             bedrooms=2),
    ),
]


def test_all_eight_real_run_093323_cross_portal_pairs_collapse() -> None:
    for left, right in RUN_093323_PAIRS:
        visible, report = deduplicate_public_feed([left, right])
        assert len(visible) == 1, (left["id"], right["id"])
        assert report["hidden_duplicates"] == 1
        assert report["groups"] == 1
        assert {link["source"] for link in visible[0]["also_on"]} == {left["source"], right["source"]}


def test_same_residence_and_signature_but_different_listing_text_stays_visible() -> None:
    common = dict(title="Appartement T3 Résidence Flamboyants", price=1100, surface=70,
                  rooms=3, city="Saint-Denis", property_type="Appartement", bedrooms=2,
                  residence="Les Flamboyants")
    left = _row("portal-a:lot-12", description="Appartement au premier étage, vue cour, sans terrasse. Lot 12 disponible immédiatement.", floor=1, **common)
    right = _row("portal-b:lot-37", description="Appartement au troisième étage, vue mer, grande terrasse. Lot 37 disponible en décembre.", floor=3, **common)
    visible, report = deduplicate_public_feed([left, right])
    assert len(visible) == 2
    assert report["hidden_duplicates"] == 0



def test_postflight_reuses_the_public_dedup_engine_instead_of_title_signature() -> None:
    source = (ROOT / "scripts" / "postflight_public_contract.py").read_text(encoding="utf-8")
    assert "from src.public_feed_dedup import deduplicate_public_feed" in source
    assert "remaining_visible, remaining_dedup = deduplicate_public_feed(active_listings)" in source
    assert "duplicate_signatures" not in source
