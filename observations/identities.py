# Human-review allowlist, based on the original Otje contract.
# Porzana/Meleagris added after owner-confirmed backyard chicken misidentifications.
# Eligibility only: never automatically infer an identity from species/evidence.
OTJE_SPECIES = frozenset({
    "Gallus gallus", "Gallus gallus domesticus", "Gallus domesticus",
    "Porzana porzana", "Meleagris gallopavo",
    "Gallus sonneratii", "Gallus lafayettii", "Gallus varius",
})
