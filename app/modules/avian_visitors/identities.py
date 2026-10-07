"""Explicit local presentation identities; never inferred from species data."""

PROFILES = {
    "otje": {
        "id": "otje",
        "display_name": "Otje",
        "subtitle": "Barnevelder",
        "source_species": frozenset({
            "Gallus gallus", "Gallus gallus domesticus", "Gallus domesticus",
            "Gallus sonneratii", "Gallus lafayettii", "Gallus varius",
        }),
        "images": {"perched": "otje.png", "flight": "otje-2.png"},
        "summary_nl": (
            "De barnevelder is een middelzwaar kippenras dat zijn oorsprong heeft in het "
            "Nederlandse Barneveld. Het ras ontstond door het inkruisen van legkippen met "
            "grote Aziatische kippenrassen, naar verluidt cochins, croad langshans en "
            "brahma's. Vanaf begin jaren 1920 werden de eerste dieren geëxporteerd naar Engeland."
        ),
        "fact_nl": (
            "Otje is één van de drie kippen die we kregen van Janneke en Machiel. Haar zussen "
            "Noortje (rood-zwart) en Betje (wit-zwart) zijn helaas al overleden. Het kakelende "
            "trio wist het gezegde 'van de leg' waar te maken. Al voordat ze bij ons kwamen, "
            "legde ze al geen eieren. Er is één keer een windei in de ren aangetroffen."
        ),
    },
}


def resolve(identity_id, scientific_name):
    profile = PROFILES.get(identity_id)
    return profile if profile and scientific_name in profile["source_species"] else None
