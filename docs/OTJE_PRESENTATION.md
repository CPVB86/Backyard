# Otje presentation

Only an explicit human `review.identity_override = "otje"` selects Otje.
The shared eligibility list remains authoritative; species alone never maps to Otje.

AvianVisitors, Atlas, presentation snapshots and TV collage aggregate these accepted
observations into one display identity, using `Gallus gallus` as the canonical
presentation/asset key and the existing `otje_perched` / `otje_flight` assets.
Counts and first/last timestamps include all eligible marked observations.
The Otje detail page includes their evidence. Unmarked observations remain separate.
Stored detector species, audio, event IDs and correction history are unchanged.
No migration or repeat confirmation is required.

Deploy the backend and restart `backyard-api.service`. The next collage export
uses the updated identity grouping. Deploy the WordPress plugin changes separately;
its correction editor offers Otje as an individual-identity choice in the species menu.
